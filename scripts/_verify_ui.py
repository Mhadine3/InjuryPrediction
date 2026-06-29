"""Headless smoke-test of the dashboard using the container's Chromium.
Loads the app, captures console errors, and checks key UI renders."""
import asyncio
from playwright.async_api import async_playwright

async def main():
    errors, logs = [], []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        pg = await b.new_page()
        pg.on("console", lambda m: (logs.append(m.text) if m.type != "error" else errors.append(m.text)))
        pg.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        await pg.goto("http://localhost:8000/", wait_until="networkidle", timeout=60000)
        await pg.wait_for_timeout(3000)
        gp = await pg.eval_on_selector_all("#group-picker .grp-pill", "els => els.length")
        sp = await pg.eval_on_selector_all("#group-picker .stage-pill", "els => els.length")
        ts = await pg.eval_on_selector_all("#team-selector button", "els => els.length")
        # Full bracket
        await pg.evaluate("viewFullBracket()")
        await pg.wait_for_timeout(400)
        cols = await pg.eval_on_selector_all("#bracket-cols > div", "els => els.length")
        cards = await pg.eval_on_selector_all("#bracket-cols [onclick^='openBracketMatch']", "els => els.length")
        # Pick a stage from the nav bar (Round of 32)
        await pg.evaluate("selectStage('LAST_32')")
        await pg.wait_for_timeout(400)
        r32 = await pg.eval_on_selector_all("#bracket-cols [onclick^='openBracketMatch']", "els => els.length")
        heading = await pg.eval_on_selector("#bracket-heading", "el => el.textContent")
        print(f"group pills: {gp}  stage pills: {sp}")
        print(f"team-selector buttons (current group): {ts}")
        print(f"full bracket columns: {cols}  clickable ties: {cards}")
        print(f"R32 stage view ties: {r32}  heading: {heading!r}")
        print(f"console errors: {len(errors)}")
        for e in errors[:15]:
            print("  ERR:", e)
        await b.close()

asyncio.run(main())
