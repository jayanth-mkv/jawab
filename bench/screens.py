"""Screenshots of the deployed prototype for the deck (drives the locally installed Microsoft Edge).

    uv run --with playwright python bench/screens.py --base https://... --code XXXX --out ../deck/b2036
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

PAGES = {
    "owner": ("/owner", ".gap"),
    "dashboard": ("/dashboard", ".steps"),
    "loop": ("/dashboard", ".steps"),
    "call": ("/call", "#callBtn"),
    "site": ("/site", "details"),
    "home": ("/", ".flow"),
}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1272)
    ap.add_argument("--height", type=int, default=500)
    ap.add_argument("--call-audio", default="", help="WAV of a customer question; places a real call")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        ctx = await browser.new_context(viewport={"width": a.width, "height": a.height}, device_scale_factor=1.5)
        for name, (path, sel) in PAGES.items():
            page = await ctx.new_page()
            await page.goto(f"{a.base}{path}?code={a.code}", wait_until="domcontentloaded")
            await page.wait_for_selector(sel, timeout=20000)
            await page.wait_for_timeout(1200)
            if name == "site":
                await page.evaluate("(document.querySelector('.new') || document.body).closest('details')?.scrollIntoView({block: 'center'})")
                await page.wait_for_timeout(300)
            if name == "loop":
                await page.locator("#gaps").scroll_into_view_if_needed()
                await page.evaluate("document.querySelector('#gaps').closest('.card').scrollIntoView({block: 'start'})")
                await page.wait_for_timeout(300)
            await page.screenshot(path=str(out / f"shot-{name}.png"))
            print("saved", name)
            await page.close()
        await browser.close()
        if a.call_audio:
            await live_call(p, a, out)


async def live_call(p, a, out: Path) -> None:
    """Place a real call: Edge's fake microphone plays a recorded customer question into Gemini Live."""
    wav = str(Path(a.call_audio).resolve())
    browser = await p.chromium.launch(channel="msedge", headless=True, args=[
        "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={wav}%noloop", "--autoplay-policy=no-user-gesture-required"])
    ctx = await browser.new_context(viewport={"width": a.width, "height": 640}, device_scale_factor=1.5, permissions=["microphone"])
    page = await ctx.new_page()
    await page.goto(f"{a.base}/call?code={a.code}", wait_until="domcontentloaded")
    await page.click("#callBtn")
    try:
        await page.wait_for_selector(".bub.sys", timeout=45000)  # "Gap reported -> owner notified"
    except Exception:
        print("no gap chip within 45 s; capturing anyway")
    await page.wait_for_timeout(4000)
    await page.locator(".phone").scroll_into_view_if_needed()
    await page.locator(".phone").screenshot(path=str(out / "shot-livecall.png"))
    await page.screenshot(path=str(out / "shot-call.png"))
    print("saved live call")
    await page.click("#endBtn")
    await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
