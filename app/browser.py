"""One shared headless Chromium, used only for JS-rendered sites the plain HTTP scrape can't read.

Playwright's async API lives on a dedicated event-loop thread; worker threads submit page loads to it,
so the browser launches once and each brand only costs a lightweight tab.
"""
import asyncio
import threading

from playwright.async_api import async_playwright

_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()
_ctx = None
_lock = asyncio.Lock()
_tabs = None  # semaphore created on the loop


async def _context():
    global _ctx, _tabs
    async with _lock:
        if _ctx is None:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox"])
            _ctx = await browser.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                                             "AppleWebKit/537.36 Chrome/126 Safari/537.36")
            await _ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in
                             ("image", "media", "font", "stylesheet") else r.continue_())
            _tabs = asyncio.Semaphore(6)
    return _ctx


async def _render(urls: list[str]) -> list[str]:
    ctx = await _context()
    async with _tabs:
        page = await ctx.new_page()
        out = []
        try:
            for url in urls:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=12000)
                    await page.wait_for_timeout(1200)  # let client-side JS fill in the footer
                    out.append(await page.content())
                except Exception:
                    continue
        finally:
            await page.close()
        return out


def render(urls: list[str], timeout: float = 40) -> list[str]:
    """Blocking call usable from any thread; returns rendered HTML for the URLs that loaded."""
    try:
        return asyncio.run_coroutine_threadsafe(_render(urls), _loop).result(timeout)
    except Exception:
        return []
