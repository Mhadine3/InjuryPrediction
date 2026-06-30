"""Headless smoke-test of the dashboard using the container's Chromium.
Checks the header nav, knockout stage bar, per-stage team counts, the team
Road-to-the-Final strip, and the projected bracket — plus console errors."""
import asyncio
from playwright.async_api import async_playwright

async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        pg = await b.new_page()
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        await pg.goto("http://localhost:8000/", wait_until="networkidle", timeout=60000)
        # wait for the async bracket projection to populate the stage bar
        try:
            await pg.wait_for_function("document.querySelectorAll('#stage-bar .stage-pill').length>=5", timeout=20000)
        except Exception:
            pass
        await pg.wait_for_timeout(1500)

        nav = await pg.eval_on_selector_all("header nav .view-tab", "els => els.length")
        stages = await pg.eval_on_selector_all("#stage-bar .stage-pill", "els => els.length")
        r32 = await pg.eval_on_selector_all("#team-selector .team-tab", "els => els.length")
        road = await pg.eval_on_selector_all("#team-road .rounded-lg", "els => els.length")
        road_txt = await pg.eval_on_selector("#team-road", "el => el ? el.textContent.replace(/\\s+/g,' ').trim().slice(0,160) : ''")
        # switch stages, check team counts shrink correctly
        await pg.evaluate("selectStage('LAST_16')"); await pg.wait_for_timeout(300)
        r16 = await pg.eval_on_selector_all("#team-selector .team-tab", "els => els.length")
        await pg.evaluate("selectStage('FINAL')"); await pg.wait_for_timeout(300)
        fin = await pg.eval_on_selector_all("#team-selector .team-tab", "els => els.length")
        # bracket view
        await pg.evaluate("viewFullBracket()"); await pg.wait_for_timeout(400)
        cols = await pg.eval_on_selector_all("#bracket-cols > div", "els => els.length")

        print(f"header nav tabs: {nav}")
        print(f"stage pills: {stages}")
        print(f"R32 team chips: {r32} | R16: {r16} | Final: {fin}")
        print(f"road cells (default team): {road}")
        print(f"road text: {road_txt!r}")
        print(f"bracket columns: {cols}")
        print(f"console errors: {len(errors)}")
        for e in errors[:12]:
            print("  ERR:", e)
        await b.close()

asyncio.run(main())
