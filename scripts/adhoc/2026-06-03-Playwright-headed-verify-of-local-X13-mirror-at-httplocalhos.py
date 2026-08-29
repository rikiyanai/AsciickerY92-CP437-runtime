# Ad hoc script: Playwright headed verify of local X13 mirror at http://localhost:8013/ — load index, wait for canvas paint, screenshot, check error counts
# Created: 2026-06-03
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Verify local X13 mirror (docs/research/x13_source/) is playable.

Loads http://localhost:8013/, waits ~3 s for boot, captures a screenshot,
collects console errors and failed network requests, reports pass/fail.
"""
import asyncio, sys, json
from playwright.async_api import async_playwright

URL = "http://localhost:8013/"
OUT = "/tmp/x13_mirror_verify.png"

async def main():
    errors = []
    failed_requests = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
        page.on("console", lambda m: errors.append(("console:"+m.type, m.text)) if m.type in ("error","warning") else None)
        page.on("requestfailed", lambda r: failed_requests.append((r.url, r.failure)))
        try:
            await page.goto(URL, wait_until="networkidle", timeout=15000)
        except Exception as e:
            print(f"goto failed: {e}", file=sys.stderr)
        await page.wait_for_timeout(3000)
        # Probe canvas paint: check ak_screen has been initialized
        canvas_w = await page.evaluate("() => { const c = document.getElementById('ascii'); return c ? c.width : -1; }")
        ak_ready = await page.evaluate("() => typeof ak_World !== 'undefined' && typeof ak_screen !== 'undefined'")
        await page.screenshot(path=OUT, full_page=False)
        await browser.close()

    print(json.dumps({
        "url": URL,
        "screenshot": OUT,
        "canvas_width": canvas_w,
        "ak_globals_ready": ak_ready,
        "errors": errors[:20],
        "failed_requests": [{"url": u, "failure": str(f)} for u,f in failed_requests[:20]],
        "error_count": len(errors),
        "failed_request_count": len(failed_requests),
    }, indent=2))

asyncio.run(main())
