"""
heatmap_sofascore.py — WC 2026 live heatmaps via SofaScore response interception.

ARCHITECTURE
------------
Playwright (headless Chromium) is launched once in a dedicated background thread
running its own asyncio event loop via ``asyncio.run()``.  This avoids the
well-known conflict between Playwright and uvicorn's Windows ProactorEventLoop.

FastAPI handlers call the public async API, which dispatches coroutines to the
Playwright thread via ``asyncio.run_coroutine_threadsafe`` and awaits results
through ``loop.run_in_executor`` (non-blocking to FastAPI's loop).

HOW SOFASCORE DATA IS CAPTURED
-------------------------------
SofaScore's API is behind Cloudflare.  Direct HTTP requests fail.
Instead we navigate with real Chromium and intercept the page's own API calls:

  get_team_matches   → WC fixtures page → intercept events/last/0 + events/next/0
  get_match_players  → match page       → intercept /event/{id}/lineups
  generate_heatmap   → match page       → click Player stats → click player
                                        → intercept /event/{id}/player/{id}/heatmap
"""
from __future__ import annotations

import asyncio
import io
import threading
import time
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import pandas as pd
from mplsoccer import Pitch

# ─── Constants ────────────────────────────────────────────────────────────────

WC_SEASON_ID       = 58210
WC_TOURNAMENT_PAGE = (
    "https://www.sofascore.com/tournament/football/world/fifa-world-cup/16"
    "#id:58210,tab:fixtures"
)

SS_TEAM_NAMES: dict[str, str] = {
    "BRA": "Brazil",
    "MAR": "Morocco",
    "HAI": "Haiti",
    "SCO": "Scotland",
    "FRA": "France",
    "SEN": "Senegal",
    "IRQ": "Iraq",
    "NOR": "Norway",
}

SS_TEAM_SLUGS: dict[str, str] = {
    "BRA": "brazil",
    "MAR": "morocco",
    "HAI": "haiti",
    "SCO": "scotland",
    "FRA": "france",
    "SEN": "senegal",
    "IRQ": "iraq",
    "NOR": "norway",
}

_ss_team_ids: dict[str, int] = {}         # populated from WC event responses

_team_recent_cache: dict[str, tuple[float, list]] = {}
_TEAM_RECENT_TTL = 300  # 5 min

ROOT       = Path(__file__).resolve().parents[3]
_CACHE_DIR = ROOT / "data" / "heatmaps_2026"

# ─── Playwright background thread ─────────────────────────────────────────────

_pw_loop:    asyncio.AbstractEventLoop | None = None
_pw_thread:  threading.Thread | None          = None
_pw_ready    = threading.Event()
_pw_browser                                   = None
_pw_context                                   = None
_pw_lock:    asyncio.Lock | None              = None   # lives on _pw_loop


def _pw_worker() -> None:
    """Entry point for the Playwright background thread."""
    global _pw_loop, _pw_browser, _pw_context, _pw_lock
    _pw_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_pw_loop)
    _pw_loop.run_until_complete(_pw_init())
    _pw_ready.set()
    _pw_loop.run_forever()


async def _pw_init() -> None:
    """Start Playwright + Chromium inside the background thread's event loop."""
    global _pw_browser, _pw_context, _pw_lock
    from playwright.async_api import async_playwright
    _pw_lock = asyncio.Lock()
    pw = await async_playwright().start()
    _pw_browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    _pw_context = await _pw_browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        viewport={"width": 1280, "height": 800},
    )
    await _pw_context.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )


def _ensure_pw_thread() -> asyncio.AbstractEventLoop:
    global _pw_thread
    if _pw_thread is None or not _pw_thread.is_alive():
        _pw_ready.clear()
        _pw_thread = threading.Thread(target=_pw_worker, daemon=True, name="pw-loop")
        _pw_thread.start()
        if not _pw_ready.wait(timeout=30):
            raise RuntimeError("Playwright thread did not start within 30 s")
    return _pw_loop


async def _dispatch(coro) -> Any:
    """
    Submit a coroutine to the Playwright thread's event loop and return its result.
    Runs ``future.result()`` in a thread-pool executor so FastAPI's loop stays free.
    """
    loop = _ensure_pw_thread()
    fut  = asyncio.run_coroutine_threadsafe(coro, loop)
    return await asyncio.get_running_loop().run_in_executor(None, fut.result, 120)


# ─── Playwright helpers (run on _pw_loop) ─────────────────────────────────────

async def _new_page():
    return await _pw_context.new_page()


async def _dismiss_consent(page) -> None:
    await page.evaluate("""
        () => {
            document.querySelectorAll(
                '.fc-consent-root, .fc-dialog-overlay, [class*="consent-"], [class*="cookie-consent"]'
            ).forEach(el => el.remove());
        }
    """)
    await asyncio.sleep(0.3)


async def _wait_for_json(page, fragment: str, timeout: float = 20.0) -> dict:
    """Register a response listener, return JSON when the URL contains `fragment`."""
    loop   = asyncio.get_running_loop()   # _pw_loop
    future = loop.create_future()

    async def on_resp(response):
        if fragment in response.url and not future.done():
            try:
                future.set_result(await response.json())
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)

    page.on("response", on_resp)
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    finally:
        page.remove_listener("response", on_resp)


# ─── In-memory TTL caches ─────────────────────────────────────────────────────

_matches_cache: dict[str, tuple[float, list]] = {}
_MATCHES_TTL = 300   # 5 min

_players_cache: dict[int, tuple[float, list]] = {}
_PLAYERS_TTL = 120   # 2 min

_player_history_cache: dict[int, tuple[float, list]] = {}
_HISTORY_TTL = 300   # 5 min

_shotmap_cache: dict[int, tuple[float, list]] = {}
_SHOTMAP_TTL = 600   # 10 min

_match_meta: dict[int, dict] = {}        # event_id → {url, home_team, away_team, date}
_player_slugs: dict[int, str] = {}       # player_id → SofaScore slug (for profile URL)


# ─── Implementation coroutines (run on _pw_loop) ──────────────────────────────

async def _impl_get_team_matches(tla: str) -> list[dict]:
    team_name = SS_TEAM_NAMES.get(tla)
    if not team_name:
        return []

    now = time.time()
    if tla in _matches_cache:
        ts, cached = _matches_cache[tla]
        if now - ts < _MATCHES_TTL:
            return cached

    page = await _new_page()
    matches: list[dict] = []
    finished_fut = asyncio.get_running_loop().create_future()
    upcoming_fut = asyncio.get_running_loop().create_future()

    async def on_resp(response):
        url = response.url
        if f"/season/{WC_SEASON_ID}/events/last/0" in url and not finished_fut.done():
            try:
                finished_fut.set_result(await response.json())
            except Exception as e:
                if not finished_fut.done():
                    finished_fut.set_exception(e)
        if f"/season/{WC_SEASON_ID}/events/next/0" in url and not upcoming_fut.done():
            try:
                upcoming_fut.set_result(await response.json())
            except Exception as e:
                if not upcoming_fut.done():
                    upcoming_fut.set_exception(e)

    try:
        page.on("response", on_resp)
        await page.goto(WC_TOURNAMENT_PAGE, timeout=30_000, wait_until="networkidle")

        finished_data: dict = {}
        upcoming_data: dict = {}
        try:
            finished_data = await asyncio.wait_for(asyncio.shield(finished_fut), timeout=20.0)
        except asyncio.TimeoutError:
            pass
        try:
            upcoming_data = await asyncio.wait_for(asyncio.shield(upcoming_fut), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        def _parse(events: list, status: str) -> None:
            for ev in events:
                ht = ev.get("homeTeam", {}).get("name", "")
                at = ev.get("awayTeam", {}).get("name", "")
                if team_name not in (ht, at):
                    continue
                ev_id      = int(ev["id"])
                custom_id  = ev.get("customId", "")
                ht_slug    = ev.get("homeTeam", {}).get("slug", ht.lower())
                at_slug    = ev.get("awayTeam", {}).get("slug", at.lower())
                ts_e       = ev.get("startTimestamp", 0)
                match_date = time.strftime("%Y-%m-%d", time.gmtime(ts_e))
                # Capture SofaScore team ID for later team-page navigation
                if tla not in _ss_team_ids:
                    if ht == team_name:
                        tid = ev.get("homeTeam", {}).get("id")
                    else:
                        tid = ev.get("awayTeam", {}).get("id")
                    if tid:
                        _ss_team_ids[tla] = int(tid)
                if status == "finished":
                    hs  = (ev.get("homeScore") or {}).get("current", 0)
                    as_ = (ev.get("awayScore") or {}).get("current", 0)
                    _match_meta[ev_id] = {
                        "url": (
                            f"https://www.sofascore.com/{ht_slug}-vs-{at_slug}"
                            f"/{custom_id}#id:{ev_id}"
                        ),
                        "home_team": ht, "away_team": at, "date": match_date,
                    }
                    matches.append({
                        "event_id": ev_id, "home_team": ht, "away_team": at,
                        "home_score": hs, "away_score": as_,
                        "date": match_date, "status": "finished",
                    })
                else:
                    matches.append({
                        "event_id": ev_id, "home_team": ht, "away_team": at,
                        "home_score": None, "away_score": None,
                        "date": match_date, "status": "upcoming",
                    })

        _parse(finished_data.get("events", []), "finished")
        _parse(upcoming_data.get("events", []), "upcoming")
    finally:
        page.remove_listener("response", on_resp)
        await page.close()

    finished = sorted([m for m in matches if m["status"] == "finished"],
                      key=lambda m: m["date"], reverse=True)
    upcoming = sorted([m for m in matches if m["status"] == "upcoming"],
                      key=lambda m: m["date"])
    result = finished + upcoming
    _matches_cache[tla] = (now, result)
    return result


async def _impl_get_match_players(event_id: int) -> list[dict]:
    now = time.time()
    if event_id in _players_cache:
        ts, cached = _players_cache[event_id]
        if now - ts < _PLAYERS_TTL:
            return cached

    meta = _match_meta.get(event_id)
    if not meta:
        raise ValueError(
            f"Match {event_id} not in cache — call /heatmap/2026/matches/{{tla}} first."
        )

    page   = await _new_page()
    result: list[dict] = []
    try:
        fut = asyncio.get_running_loop().create_future()

        async def on_resp(response):
            if f"/event/{event_id}/lineups" in response.url and not fut.done():
                try:
                    fut.set_result(await response.json())
                except Exception as e:
                    if not fut.done():
                        fut.set_exception(e)

        page.on("response", on_resp)
        await page.goto(meta["url"], timeout=30_000, wait_until="networkidle")
        data = await asyncio.wait_for(asyncio.shield(fut), timeout=20.0)

        # Use cached team names from match meta as fallback (lineup data may omit them)
        side_labels = {
            "home": meta.get("home_team", "Home"),
            "away": meta.get("away_team", "Away"),
        }

        for side in ("home", "away"):
            side_data = data.get(side, {})
            team_name = (side_data.get("team", {}) or {}).get("name") or side_labels[side]
            for p in side_data.get("players", []):
                pid   = p.get("player", {}).get("id")
                pname = p.get("player", {}).get("name", "")
                pslug = p.get("player", {}).get("slug", "")
                if pid and pname:
                    pid = int(pid)
                    if pslug:
                        _player_slugs[pid] = pslug   # cache for player history lookup
                    result.append({
                        "player_id":   pid,
                        "player_name": pname,
                        "team_name":   team_name,
                    })
    finally:
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass
        await page.close()

    result.sort(key=lambda x: (x["team_name"], x["player_name"]))
    _players_cache[event_id] = (now, result)
    return result


async def _impl_generate_heatmap_bytes(
    player_name: str, player_id: int, event_id: int
) -> bytes:
    cache_path = _CACHE_DIR / f"ss_{event_id}_{player_id}.png"
    if cache_path.exists():
        return cache_path.read_bytes()

    meta = _match_meta.get(event_id)
    if not meta:
        raise ValueError(
            f"Match {event_id} not in cache — call /heatmap/2026/matches/{{tla}} first."
        )

    page   = await _new_page()
    points: list[dict] = []
    try:
        fut = asyncio.get_running_loop().create_future()

        async def on_resp(response):
            if (f"/event/{event_id}/player/{player_id}/heatmap" in response.url
                    and not fut.done()):
                try:
                    fut.set_result(await response.json())
                except Exception as e:
                    if not fut.done():
                        fut.set_exception(e)

        page.on("response", on_resp)
        await page.goto(meta["url"], timeout=30_000, wait_until="networkidle")
        await _dismiss_consent(page)

        stats_tab = await page.wait_for_selector(
            "a:has-text('Player stats'), button:has-text('Player stats')",
            timeout=10_000,
        )
        await stats_tab.click()
        await asyncio.sleep(2)

        player_link = await page.wait_for_selector(
            f"a[href$='/{player_id}']",
            timeout=10_000,
        )
        await player_link.click()

        data   = await asyncio.wait_for(asyncio.shield(fut), timeout=15.0)
        points = data.get("heatmap", [])
    finally:
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass
        await page.close()

    if len(points) < 5:
        raise ValueError(
            f"Not enough heatmap data for '{player_name}' ({len(points)} points)."
        )

    df          = pd.DataFrame(points)
    match_label = f"{meta['home_team']} vs {meta['away_team']}  ·  {meta['date']}"
    png_bytes   = _render_png(df, player_name, match_label)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(png_bytes)
    return png_bytes


async def _impl_get_player_matches(player_id: int) -> list[dict]:
    """
    Return the last 10 finished matches for a player (any competition).
    Navigates to their SofaScore profile page and intercepts events/last/0.
    Stores match URLs in _match_meta so heatmaps can be generated afterwards.
    """
    now = time.time()
    if player_id in _player_history_cache:
        ts, cached = _player_history_cache[player_id]
        if now - ts < _HISTORY_TTL:
            return cached

    slug = _player_slugs.get(player_id, "player")
    profile_url = f"https://www.sofascore.com/player/{slug}/{player_id}"

    page    = await _new_page()
    matches: list[dict] = []
    fut     = asyncio.get_running_loop().create_future()

    async def on_resp(response):
        if f"/player/{player_id}/events/last/0" in response.url and not fut.done():
            try:
                fut.set_result(await response.json())
            except Exception as e:
                if not fut.done():
                    fut.set_exception(e)

    try:
        page.on("response", on_resp)
        await page.goto(profile_url, timeout=25_000, wait_until="networkidle")
        data = await asyncio.wait_for(asyncio.shield(fut), timeout=15.0)

        for ev in data.get("events", []):
            if ev.get("status", {}).get("type") != "finished":
                continue
            ev_id      = int(ev["id"])
            custom_id  = ev.get("customId", "")
            ht         = ev.get("homeTeam", {}).get("name", "")
            at         = ev.get("awayTeam", {}).get("name", "")
            ht_slug    = ev.get("homeTeam", {}).get("slug", "home")
            at_slug    = ev.get("awayTeam", {}).get("slug", "away")
            hs         = (ev.get("homeScore") or {}).get("current", 0)
            as_        = (ev.get("awayScore") or {}).get("current", 0)
            ts_e       = ev.get("startTimestamp", 0)
            match_date = time.strftime("%Y-%m-%d", time.gmtime(ts_e))
            competition = (
                ev.get("tournament", {}).get("uniqueTournament", {}).get("name")
                or ev.get("tournament", {}).get("name", "")
            )

            _match_meta[ev_id] = {
                "url": (
                    f"https://www.sofascore.com/{ht_slug}-vs-{at_slug}"
                    f"/{custom_id}#id:{ev_id}"
                ),
                "home_team": ht, "away_team": at, "date": match_date,
            }
            matches.append({
                "event_id":    ev_id,
                "home_team":   ht,
                "away_team":   at,
                "home_score":  hs,
                "away_score":  as_,
                "date":        match_date,
                "competition": competition,
                "status":      "finished",
            })
    except asyncio.TimeoutError:
        pass   # return whatever we collected
    finally:
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass
        await page.close()

    matches.sort(key=lambda m: m["date"], reverse=True)
    result = matches[:10]
    _player_history_cache[player_id] = (now, result)
    return result


async def _fetch_shotmap_for_event(event_id: int) -> list[dict]:
    """
    Return all shots for a single match (both teams) from SofaScore shotmap API.
    Results are cached in _shotmap_cache for _SHOTMAP_TTL seconds.
    Returns [] if the match URL is unknown or SofaScore has no shotmap yet.
    """
    now = time.time()
    if event_id in _shotmap_cache:
        ts, cached = _shotmap_cache[event_id]
        if now - ts < _SHOTMAP_TTL:
            return cached

    meta = _match_meta.get(event_id)
    if not meta:
        return []

    page = await _new_page()
    shots_raw: list[dict] = []
    fut = asyncio.get_running_loop().create_future()

    async def on_resp(response):
        if f"/event/{event_id}/shotmap" in response.url and not fut.done():
            try:
                fut.set_result(await response.json())
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)

    try:
        page.on("response", on_resp)
        await page.goto(meta["url"], timeout=30_000, wait_until="networkidle")
        await _dismiss_consent(page)

        # Shotmap is lazy-loaded; try Statistics or Shots tab to trigger it
        try:
            tab = await page.wait_for_selector(
                "a:has-text('Statistics'), button:has-text('Statistics'), "
                "a:has-text('Shots'), button:has-text('Shots')",
                timeout=7_000,
            )
            await tab.click()
            await asyncio.sleep(1.5)
        except Exception:
            pass

        try:
            data = await asyncio.wait_for(asyncio.shield(fut), timeout=15.0)
            shots_raw = data.get("shotmap", [])
        except asyncio.TimeoutError:
            shots_raw = []
    finally:
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass
        await page.close()

    _shotmap_cache[event_id] = (now, shots_raw)
    return shots_raw


_VALID_OUTCOMES = {"goal", "save", "block", "miss"}


def _normalise_shot(s: dict, is_home: bool) -> dict | None:
    """
    Convert a raw SofaScore shot dict to a normalised row where the team
    always attacks left → right (x increases toward opponent's goal).
    Returns None if coords are missing.
    """
    coords = s.get("playerCoordinates") or {}
    if "x" not in coords:
        return None
    x = float(coords["x"])
    y = float(coords["y"])
    if not is_home:
        x, y = 100.0 - x, 100.0 - y
    outcome = (s.get("shotType") or "miss").lower()
    if outcome not in _VALID_OUTCOMES:
        outcome = "miss"
    return {
        "x":        x,
        "y":        y,
        "outcome":  outcome,
        "xg":       float(s.get("xg") or 0.0),
        "player":   (s.get("player") or {}).get("name", ""),
        "bodyPart": s.get("bodyPart", ""),
    }


async def _impl_get_danger_zones_png(event_id: int, tla: str) -> bytes:
    """
    Team danger zones for a single WC 2026 match:
      Left  — shot scatter (colored by outcome, sized by xG)
      Right — xG-weighted zone grid
    """
    tla = tla.upper()
    cache_path = _CACHE_DIR / f"dz_{event_id}_{tla}.png"
    if cache_path.exists():
        return cache_path.read_bytes()

    meta = _match_meta.get(event_id)
    if not meta:
        raise ValueError(
            f"Match {event_id} not in cache — call /heatmap/2026/matches/{{tla}} first."
        )

    shots_raw = await _fetch_shotmap_for_event(event_id)
    if not shots_raw:
        raise ValueError(
            f"No shot data for match {event_id}. "
            "SofaScore may not have published a shotmap yet."
        )

    team_name = SS_TEAM_NAMES.get(tla, tla)
    is_home   = meta.get("home_team") == team_name

    team_shots = []
    for s in shots_raw:
        if s.get("isHome", True) != is_home:
            continue
        row = _normalise_shot(s, is_home)
        if row:
            team_shots.append(row)

    if not team_shots:
        raise ValueError(f"No shots found for {tla} in match {event_id}.")

    df = pd.DataFrame(team_shots)
    match_label = f"{meta['home_team']} vs {meta['away_team']}  ·  {meta['date']}"
    png_bytes = _render_danger_zones_png(df, team_name, match_label)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(png_bytes)
    return png_bytes


async def _impl_get_player_danger_zones_png(
    player_id: int, player_name: str
) -> bytes:
    """
    Player shot profile aggregated across their last 10 matches in any competition.

    Fetches up to 10 shotmaps concurrently (max 3 Playwright pages at once) and
    filters every shot where shot.player.id == player_id, then normalises direction
    using the shot's own isHome flag so all shots point left → right.
    """
    cache_path = _CACHE_DIR / f"pdz_{player_id}.png"

    # Get player's recent matches (from in-memory cache or fetch fresh)
    matches: list[dict] = []
    hist = _player_history_cache.get(player_id)
    if hist:
        _, matches = hist
    if not matches:
        matches = await _impl_get_player_matches(player_id)
    if not matches:
        raise ValueError(
            f"No match history found for player {player_id}. "
            "Select the player from a WC match first to populate history."
        )

    # Invalidate disk cache if player has played new matches since last render
    newest_match_date = matches[0]["date"] if matches else ""
    marker_path = _CACHE_DIR / f"pdz_{player_id}.date"
    if (
        cache_path.exists()
        and marker_path.exists()
        and marker_path.read_text().strip() == newest_match_date
    ):
        return cache_path.read_bytes()

    # Fetch all shotmaps concurrently — cap at 3 pages to avoid overloading browser
    sem = asyncio.Semaphore(3)

    async def _fetch_one(ev_id: int) -> list[dict]:
        async with sem:
            return await _fetch_shotmap_for_event(ev_id)

    results = await asyncio.gather(
        *[_fetch_one(m["event_id"]) for m in matches],
        return_exceptions=True,
    )

    all_shots: list[dict] = []
    n_matches_used = 0
    competitions: set[str] = set()

    for match, shots_or_exc in zip(matches, results):
        if isinstance(shots_or_exc, Exception) or not shots_or_exc:
            continue
        shots_list: list[dict] = shots_or_exc

        # Filter shots belonging to this player
        player_shots = [
            s for s in shots_list
            if (s.get("player") or {}).get("id") == player_id
        ]
        if not player_shots:
            continue

        n_matches_used += 1
        comp = match.get("competition", "")
        if comp:
            competitions.add(comp)

        for s in player_shots:
            row = _normalise_shot(s, bool(s.get("isHome", True)))
            if row:
                row["match"]       = f"{match['home_team']} vs {match['away_team']}"
                row["date"]        = match["date"]
                row["competition"] = comp
                all_shots.append(row)

    if not all_shots:
        raise ValueError(
            f"No shots found for {player_name} across the {len(matches)} fetched matches. "
            "SofaScore may not yet have shotmaps for those competitions."
        )

    df = pd.DataFrame(all_shots)
    comp_list = ", ".join(sorted(competitions)[:3])
    if len(competitions) > 3:
        comp_list += f" +{len(competitions) - 3} more"
    subtitle = (
        f"{n_matches_used} match{'es' if n_matches_used != 1 else ''} · "
        f"{comp_list or 'mixed competitions'}"
    )

    png_bytes = _render_player_danger_zones_png(df, player_name, subtitle)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(png_bytes)
    marker_path.write_text(newest_match_date)
    return png_bytes


# ─── Public async API (called from FastAPI handlers) ──────────────────────────

async def get_team_matches(tla: str) -> list[dict]:
    return await _dispatch(_impl_get_team_matches(tla.upper()))


async def get_match_players(event_id: int) -> list[dict]:
    return await _dispatch(_impl_get_match_players(event_id))


async def get_player_matches(player_id: int) -> list[dict]:
    return await _dispatch(_impl_get_player_matches(player_id))


async def generate_heatmap_bytes(
    player_name: str, player_id: int, event_id: int
) -> bytes:
    return await _dispatch(_impl_generate_heatmap_bytes(player_name, player_id, event_id))


async def get_danger_zones_png(event_id: int, tla: str) -> bytes:
    return await _dispatch(_impl_get_danger_zones_png(event_id, tla.upper()))


async def get_player_danger_zones_png(player_id: int, player_name: str) -> bytes:
    return await _dispatch(_impl_get_player_danger_zones_png(player_id, player_name))


async def get_team_recent_matches(tla: str) -> list[dict]:
    return await _dispatch(_impl_get_team_recent_matches(tla.upper()))


async def get_team_aggregate_danger_zones_png(tla: str) -> bytes:
    return await _dispatch(_impl_get_team_aggregate_danger_zones_png(tla.upper()))


# ─── PNG rendering ────────────────────────────────────────────────────────────

def _render_png(df: pd.DataFrame, player_name: str, match_label: str) -> bytes:
    n = len(df)

    fig, ax = plt.subplots(figsize=(14, 9.5))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    pitch = Pitch(
        pitch_type="opta",
        pitch_color="#1a1f2e",
        line_color="#3d4a6b",
        linewidth=1.5,
        goal_type="box",
        corner_arcs=True,
    )
    pitch.draw(ax=ax)
    pitch.kdeplot(
        df["x"], df["y"], ax=ax,
        cmap="YlOrRd", fill=True, levels=100,
        thresh=0.04, alpha=0.75, zorder=2,
    )
    pitch.scatter(
        df["x"], df["y"], ax=ax,
        s=18, color="white", alpha=0.25, zorder=3, edgecolors="none",
    )

    title = (
        f"{player_name}\n"
        f"{match_label}  ·  {n} tracked positions\n"
        "FIFA World Cup 2026  ·  Data: SofaScore"
    )
    fig.text(
        0.5, 0.975, title,
        ha="center", va="top", fontsize=12, color="white",
        fontweight="bold", linespacing=1.65,
        path_effects=[pe.withStroke(linewidth=3, foreground="#0d1117")],
    )
    ax.annotate(
        "defending  <                                      >  attacking",
        xy=(0.5, 0.025), xycoords="axes fraction",
        ha="center", va="bottom", fontsize=8.5, color="#556688",
        fontfamily="monospace",
    )

    sm = plt.cm.ScalarMappable(cmap="YlOrRd")
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, orientation="vertical",
                        fraction=0.018, pad=0.02, shrink=0.7)
    cbar.ax.yaxis.set_tick_params(color="#8899bb", labelcolor="#8899bb", labelsize=8)
    cbar.set_label("Touch density", color="#8899bb", fontsize=9)
    cbar.outline.set_edgecolor("#3d4a6b")

    fig.text(
        0.01, 0.008,
        "Source: SofaScore  |  FIFA World Cup 2026  |  live scouting",
        ha="left", va="bottom", fontsize=7, color="#334466",
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _render_danger_zones_png(
    df: pd.DataFrame,
    team_name: str,
    match_label: str,
    competition_label: str = "FIFA World Cup 2026",
) -> bytes:
    """
    Two-panel danger-zones figure:
      Left  — shot scatter on half-pitch (outcome colour + xG size)
      Right — xG-weighted zone grid on half-pitch
    Both panels use Opta coords, normalised so the team attacks left → right.
    """
    n_shots  = len(df)
    n_goals  = int((df["outcome"] == "goal").sum())
    total_xg = float(df["xg"].sum())

    OUTCOME: dict[str, dict] = {
        "goal":  dict(color="#00e676", marker="*", scale=4.5, z=5, label="Goal"),
        "save":  dict(color="#40c4ff", marker="o", scale=2.5, z=4, label="Saved"),
        "block": dict(color="#ffca28", marker="s", scale=2.0, z=3, label="Blocked"),
        "miss":  dict(color="#ef5350", marker="X", scale=2.0, z=3, label="Off target"),
    }

    pitch_kw = dict(
        pitch_type="opta",
        half=True,
        pitch_color="#1a1f2e",
        line_color="#3d4a6b",
        linewidth=1.5,
        goal_type="box",
    )

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(22, 9))
    fig.patch.set_facecolor("#0d1117")

    p = Pitch(**pitch_kw)

    # ── Left panel: shot scatter ─────────────────────────────────────────────
    p.draw(ax=ax1)
    ax1.set_facecolor("#1a1f2e")
    for outcome, sty in OUTCOME.items():
        sub = df[df["outcome"] == outcome]
        if sub.empty:
            continue
        sizes = 200 * sty["scale"] * (1.0 + sub["xg"] * 4)
        p.scatter(
            sub["x"], sub["y"], ax=ax1,
            s=sizes, color=sty["color"], marker=sty["marker"],
            alpha=0.88, zorder=sty["z"],
            edgecolors="white", linewidths=0.4,
            label=f"{sty['label']} ({len(sub)})",
        )
    leg = ax1.legend(
        loc="lower left", fontsize=8.5,
        facecolor="#0d1117", edgecolor="#3d4a6b", labelcolor="white",
        markerscale=0.75, framealpha=0.85,
    )
    ax1.set_title(
        f"Shot Locations — {n_shots} shots · {n_goals} goal{'s' if n_goals != 1 else ''}",
        color="white", fontsize=11, pad=10, fontweight="bold",
    )
    ax1.annotate(
        "← defending          attacking →",
        xy=(0.5, 0.015), xycoords="axes fraction",
        ha="center", va="bottom", fontsize=7.5, color="#556688", fontfamily="monospace",
    )

    # ── Right panel: xG zone grid ────────────────────────────────────────────
    p.draw(ax=ax2)
    ax2.set_facecolor("#1a1f2e")
    try:
        bin_stat = p.bin_statistic(
            df["x"], df["y"],
            values=df["xg"],
            statistic="sum", bins=(6, 5),
        )
        hm = p.heatmap(bin_stat, ax=ax2, cmap="YlOrRd", alpha=0.82)
        p.label_heatmap(
            bin_stat, ax=ax2,
            color="white", fontsize=9, fmt=".2f",
            path_effects=[pe.withStroke(linewidth=2, foreground="black")],
        )
        cb = fig.colorbar(hm, ax=ax2, fraction=0.028, pad=0.03, shrink=0.78)
        cb.ax.yaxis.set_tick_params(color="#8899bb", labelcolor="#8899bb", labelsize=7)
        cb.set_label("xG accumulated in zone", color="#8899bb", fontsize=8)
        cb.outline.set_edgecolor("#3d4a6b")
    except Exception:
        ax2.text(0.5, 0.5, "Zone grid unavailable\n(insufficient data)",
                 transform=ax2.transAxes, ha="center", va="center",
                 color="#8899bb", fontsize=10)
    ax2.set_title(
        f"xG Density by Zone — Total xG: {total_xg:.2f}",
        color="white", fontsize=11, pad=10, fontweight="bold",
    )
    ax2.annotate(
        "← defending          attacking →",
        xy=(0.5, 0.015), xycoords="axes fraction",
        ha="center", va="bottom", fontsize=7.5, color="#556688", fontfamily="monospace",
    )

    # ── Global title ─────────────────────────────────────────────────────────
    fig.text(
        0.5, 0.985,
        f"{team_name} — Danger Zones",
        ha="center", va="top", fontsize=15, color="white", fontweight="bold",
        path_effects=[pe.withStroke(linewidth=3, foreground="#0d1117")],
    )
    fig.text(
        0.5, 0.962,
        f"{match_label}  ·  FIFA World Cup 2026  ·  Data: SofaScore",
        ha="center", va="top", fontsize=9.5, color="#8899bb",
    )
    fig.text(
        0.01, 0.005,
        "Source: SofaScore  |  FIFA World Cup 2026  |  live scouting",
        ha="left", va="bottom", fontsize=7, color="#334466",
    )

    fig.subplots_adjust(top=0.90, bottom=0.04, left=0.03, right=0.97, wspace=0.07)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _render_player_danger_zones_png(
    df: pd.DataFrame,
    player_name: str,
    subtitle: str,
) -> bytes:
    """
    Two-panel shot-profile figure for a single player across multiple matches.
    Identical layout to _render_danger_zones_png but titled for the player.
    """
    n_shots  = len(df)
    n_goals  = int((df["outcome"] == "goal").sum())
    total_xg = float(df["xg"].sum())

    OUTCOME: dict[str, dict] = {
        "goal":  dict(color="#00e676", marker="*", scale=4.5, z=5, label="Goal"),
        "save":  dict(color="#40c4ff", marker="o", scale=2.5, z=4, label="Saved"),
        "block": dict(color="#ffca28", marker="s", scale=2.0, z=3, label="Blocked"),
        "miss":  dict(color="#ef5350", marker="X", scale=2.0, z=3, label="Off target"),
    }

    pitch_kw = dict(
        pitch_type="opta",
        half=True,
        pitch_color="#1a1f2e",
        line_color="#3d4a6b",
        linewidth=1.5,
        goal_type="box",
    )

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(22, 9))
    fig.patch.set_facecolor("#0d1117")

    p = Pitch(**pitch_kw)

    p.draw(ax=ax1)
    ax1.set_facecolor("#1a1f2e")
    for outcome, sty in OUTCOME.items():
        sub = df[df["outcome"] == outcome]
        if sub.empty:
            continue
        sizes = 200 * sty["scale"] * (1.0 + sub["xg"] * 4)
        p.scatter(
            sub["x"], sub["y"], ax=ax1,
            s=sizes, color=sty["color"], marker=sty["marker"],
            alpha=0.88, zorder=sty["z"],
            edgecolors="white", linewidths=0.4,
            label=f"{sty['label']} ({len(sub)})",
        )
    ax1.legend(
        loc="lower left", fontsize=8.5,
        facecolor="#0d1117", edgecolor="#3d4a6b", labelcolor="white",
        markerscale=0.75, framealpha=0.85,
    )
    ax1.set_title(
        f"Shot Locations — {n_shots} shots · {n_goals} goal{'s' if n_goals != 1 else ''}",
        color="white", fontsize=11, pad=10, fontweight="bold",
    )
    ax1.annotate(
        "← defending          attacking →",
        xy=(0.5, 0.015), xycoords="axes fraction",
        ha="center", va="bottom", fontsize=7.5, color="#556688", fontfamily="monospace",
    )

    p.draw(ax=ax2)
    ax2.set_facecolor("#1a1f2e")
    try:
        bin_stat = p.bin_statistic(
            df["x"], df["y"],
            values=df["xg"],
            statistic="sum", bins=(6, 5),
        )
        hm = p.heatmap(bin_stat, ax=ax2, cmap="YlOrRd", alpha=0.82)
        p.label_heatmap(
            bin_stat, ax=ax2,
            color="white", fontsize=9, fmt=".2f",
            path_effects=[pe.withStroke(linewidth=2, foreground="black")],
        )
        cb = fig.colorbar(hm, ax=ax2, fraction=0.028, pad=0.03, shrink=0.78)
        cb.ax.yaxis.set_tick_params(color="#8899bb", labelcolor="#8899bb", labelsize=7)
        cb.set_label("xG accumulated in zone", color="#8899bb", fontsize=8)
        cb.outline.set_edgecolor("#3d4a6b")
    except Exception:
        ax2.text(0.5, 0.5, "Zone grid unavailable\n(insufficient data)",
                 transform=ax2.transAxes, ha="center", va="center",
                 color="#8899bb", fontsize=10)
    ax2.set_title(
        f"xG Density by Zone — Total xG: {total_xg:.2f}",
        color="white", fontsize=11, pad=10, fontweight="bold",
    )
    ax2.annotate(
        "← defending          attacking →",
        xy=(0.5, 0.015), xycoords="axes fraction",
        ha="center", va="bottom", fontsize=7.5, color="#556688", fontfamily="monospace",
    )

    fig.text(
        0.5, 0.985,
        f"{player_name} — Shot Profile",
        ha="center", va="top", fontsize=15, color="white", fontweight="bold",
        path_effects=[pe.withStroke(linewidth=3, foreground="#0d1117")],
    )
    fig.text(
        0.5, 0.962,
        f"{subtitle}  ·  Data: SofaScore",
        ha="center", va="top", fontsize=9.5, color="#8899bb",
    )
    fig.text(
        0.01, 0.005,
        "Source: SofaScore  |  FIFA World Cup 2026  |  live scouting",
        ha="left", va="bottom", fontsize=7, color="#334466",
    )

    fig.subplots_adjust(top=0.90, bottom=0.04, left=0.03, right=0.97, wspace=0.07)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


async def _impl_get_team_recent_matches(tla: str) -> list[dict]:
    """
    Return the last 10 finished matches for a team (any competition).

    Strategy
    --------
    1. Ensure we have a SofaScore team ID (from WC-match cache or URL redirect).
    2. Navigate to the team's SofaScore profile page.
    3. Intercept any API response whose URL contains both the team ID and
       "events/last" or "events/previous" — covers URL-scheme variations.
    4. If the default page-load doesn't fire that request, click the
       "Results" / "Matches" tab to trigger it.
    5. If we still time out, fall back to whatever WC matches are already
       cached in _match_meta (gives at least 1 match for plotting).
    """
    now = time.time()
    if tla in _team_recent_cache:
        ts, cached = _team_recent_cache[tla]
        if now - ts < _TEAM_RECENT_TTL:
            return cached

    # ── Step 1: resolve team ID ───────────────────────────────────────────────
    team_id = _ss_team_ids.get(tla)
    slug    = SS_TEAM_SLUGS.get(tla, tla.lower())

    if not team_id:
        # Try WC match page first (populates _ss_team_ids as a side-effect)
        await _impl_get_team_matches(tla)
        team_id = _ss_team_ids.get(tla)

    if not team_id:
        # Last-resort: navigate to slug-only URL and extract ID from the redirect
        id_page = await _new_page()
        try:
            await id_page.goto(
                f"https://www.sofascore.com/team/football/{slug}",
                timeout=20_000, wait_until="domcontentloaded",
            )
            await asyncio.sleep(2)
            for part in reversed(id_page.url.rstrip("/").split("/")):
                if part.isdigit():
                    team_id = int(part)
                    _ss_team_ids[tla] = team_id
                    break
        except Exception:
            pass
        finally:
            await id_page.close()

    if not team_id:
        raise ValueError(
            f"Could not determine SofaScore team ID for {tla}. "
            "Open the Scout tab for this team first to populate WC match data."
        )

    # ── Step 2: navigate and intercept events ─────────────────────────────────
    profile_url = f"https://www.sofascore.com/team/football/{slug}/{team_id}"
    team_name   = SS_TEAM_NAMES.get(tla, tla)

    page    = await _new_page()
    matches: list[dict] = []
    fut     = asyncio.get_running_loop().create_future()

    async def on_resp(response):
        url = response.url
        # Broad match: any endpoint for this team that delivers recent/previous events
        if (
            str(team_id) in url
            and ("events/last" in url or "events/previous" in url)
            and not fut.done()
        ):
            try:
                data = await response.json()
                # Only resolve on a response that actually contains events
                if data.get("events") is not None and not fut.done():
                    fut.set_result(data)
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)

    try:
        page.on("response", on_resp)
        # Use "load" not "networkidle" — SofaScore fires the events API during
        # initial load and networkidle never completes on their heavy SPA pages.
        try:
            await page.goto(profile_url, timeout=30_000, wait_until="load")
        except Exception:
            pass  # continue even if load times out; listener may have fired
        await _dismiss_consent(page)

        # Give JS a moment to fire the events XHR after DOM is ready
        await asyncio.sleep(3)

        # If events haven't loaded yet, click the Results / Matches tab
        if not fut.done():
            try:
                tab = await page.wait_for_selector(
                    "a:has-text('Results'), button:has-text('Results'), "
                    "a:has-text('Matches'), button:has-text('Matches')",
                    timeout=6_000,
                )
                await tab.click()
                await asyncio.sleep(3)
            except Exception:
                pass

        try:
            data = await asyncio.wait_for(asyncio.shield(fut), timeout=25.0)
        except asyncio.TimeoutError:
            # ── Fallback: use WC matches already in _match_meta ───────────────
            wc_fallback: list[dict] = []
            for ev_id, meta in _match_meta.items():
                if meta.get("home_team") == team_name or meta.get("away_team") == team_name:
                    wc_fallback.append({
                        "event_id":    ev_id,
                        "home_team":   meta["home_team"],
                        "away_team":   meta["away_team"],
                        "home_score":  0,
                        "away_score":  0,
                        "date":        meta.get("date", ""),
                        "competition": "FIFA World Cup 2026",
                        "status":      "finished",
                    })
            if wc_fallback:
                wc_fallback.sort(key=lambda m: m["date"], reverse=True)
                _team_recent_cache[tla] = (now, wc_fallback)
                return wc_fallback
            raise ValueError(
                f"Timed out loading match history for {tla} from SofaScore "
                "and no WC match data is cached yet."
            )

        for ev in data.get("events", []):
            if ev.get("status", {}).get("type") != "finished":
                continue
            ev_id       = int(ev["id"])
            custom_id   = ev.get("customId", "")
            ht          = ev.get("homeTeam", {}).get("name", "")
            at          = ev.get("awayTeam", {}).get("name", "")
            ht_slug     = ev.get("homeTeam", {}).get("slug", "home")
            at_slug     = ev.get("awayTeam", {}).get("slug", "away")
            hs          = (ev.get("homeScore") or {}).get("current", 0)
            as_         = (ev.get("awayScore") or {}).get("current", 0)
            ts_e        = ev.get("startTimestamp", 0)
            match_date  = time.strftime("%Y-%m-%d", time.gmtime(ts_e))
            competition = (
                ev.get("tournament", {}).get("uniqueTournament", {}).get("name")
                or ev.get("tournament", {}).get("name", "")
            )
            _match_meta[ev_id] = {
                "url": (
                    f"https://www.sofascore.com/{ht_slug}-vs-{at_slug}"
                    f"/{custom_id}#id:{ev_id}"
                ),
                "home_team": ht, "away_team": at, "date": match_date,
            }
            matches.append({
                "event_id":    ev_id,
                "home_team":   ht,
                "away_team":   at,
                "home_score":  hs,
                "away_score":  as_,
                "date":        match_date,
                "competition": competition,
                "status":      "finished",
            })
    finally:
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass
        await page.close()

    matches.sort(key=lambda m: m["date"], reverse=True)
    result = matches[:10]
    _team_recent_cache[tla] = (now, result)
    return result


async def _impl_get_team_aggregate_danger_zones_png(tla: str) -> bytes:
    """
    Team danger zones aggregated across the last 10 matches in any competition.
    Reuses _fetch_shotmap_for_event and _render_danger_zones_png.
    """
    cache_path  = _CACHE_DIR / f"tdz_{tla}.png"
    marker_path = _CACHE_DIR / f"tdz_{tla}.date"

    team_name = SS_TEAM_NAMES.get(tla, tla)

    matches = await _impl_get_team_recent_matches(tla)
    if not matches:
        raise ValueError(
            f"No recent match history found for {tla}. "
            "Load WC matches first via /heatmap/2026/matches/{tla}."
        )

    newest_match_date = matches[0]["date"]
    if (
        cache_path.exists()
        and marker_path.exists()
        and marker_path.read_text().strip() == newest_match_date
    ):
        return cache_path.read_bytes()

    sem = asyncio.Semaphore(3)

    async def _fetch_one(ev_id: int) -> list[dict]:
        async with sem:
            return await _fetch_shotmap_for_event(ev_id)

    results = await asyncio.gather(
        *[_fetch_one(m["event_id"]) for m in matches],
        return_exceptions=True,
    )

    all_shots: list[dict] = []
    n_matches_used = 0
    competitions: set[str] = set()

    for match, shots_or_exc in zip(matches, results):
        if isinstance(shots_or_exc, Exception) or not shots_or_exc:
            continue
        shots_list: list[dict] = shots_or_exc

        is_home    = match.get("home_team") == team_name
        team_shots = [s for s in shots_list if s.get("isHome", True) == is_home]
        if not team_shots:
            continue

        n_matches_used += 1
        comp = match.get("competition", "")
        if comp:
            competitions.add(comp)

        for s in team_shots:
            row = _normalise_shot(s, is_home)
            if row:
                row["match"]       = f"{match['home_team']} vs {match['away_team']}"
                row["date"]        = match["date"]
                row["competition"] = comp
                all_shots.append(row)

    if not all_shots:
        raise ValueError(
            f"No shot data found for {tla} across {len(matches)} recent matches. "
            "SofaScore may not have shotmaps for those competitions yet."
        )

    df = pd.DataFrame(all_shots)
    comp_list = ", ".join(sorted(competitions)[:3])
    if len(competitions) > 3:
        comp_list += f" +{len(competitions) - 3} more"
    subtitle = (
        f"{n_matches_used} match{'es' if n_matches_used != 1 else ''} · "
        f"{comp_list or 'mixed competitions'}"
    )

    png_bytes = _render_danger_zones_png(df, team_name, subtitle)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(png_bytes)
    marker_path.write_text(newest_match_date)
    return png_bytes
