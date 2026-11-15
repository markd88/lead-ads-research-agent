"""Read-only Playwright inspection: fresh context, GET-only, public URLs, no clicks/fills/submits."""
from __future__ import annotations

from pathlib import Path

from .forms import extract_forms
from .models import RunState, ToolResult
from .url_policy import UrlPolicyError, validate_public_url

# Fixed, app-authored read script; the model never supplies JS.
READ_FRAMES = "() => document.documentElement.outerHTML"


class BrowserTools:
    def __init__(self, state: RunState, output_dir: Path, allowed_test_origins: frozenset[str] = frozenset()):
        self.state = state
        self.output_dir = output_dir
        self.allowed = allowed_test_origins

    async def browser_inspect(self, url: str) -> ToolResult:
        try:
            url = validate_public_url(url, self.allowed)
        except UrlPolicyError as e:
            return ToolResult(errors=[str(e)])
        if not await self.state.reserve("browser"):
            return ToolResult(errors=["browser budget exhausted"])
        from playwright.async_api import async_playwright

        out = ToolResult()
        blocked: list[str] = []

        async def guard(route):
            req = route.request
            try:
                if req.method != "GET":
                    raise UrlPolicyError(f"{req.method} blocked (read-only)")
                if req.url.startswith(("data:", "blob:")):
                    return await route.continue_()
                validate_public_url(req.url, self.allowed)
            except UrlPolicyError as e:
                blocked.append(f"{req.method} {req.url[:120]}: {e}")
                return await route.abort()
            await route.continue_()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context()
                await ctx.route("**/*", guard)
                page = await ctx.new_page()
                await page.goto(url, timeout=15000, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:  # noqa: BLE001 - bounded wait only
                    pass
                for i, frame in enumerate(page.frames):
                    try:
                        html = await frame.evaluate(READ_FRAMES)
                    except Exception:  # noqa: BLE001
                        continue
                    forms = extract_forms(html, frame.url if i else url, self.state.evidence, method="browser_dom")
                    out.forms.extend(forms)
                shots = self.output_dir / "screenshots"
                shots.mkdir(parents=True, exist_ok=True)
                shot = shots / f"browser_{self.state.attempts.get('browser', 0)}.png"
                await page.screenshot(path=str(shot), full_page=False)
                self.state.log("browser_inspect", url=url, frames=len(page.frames), screenshot=str(shot),
                               forms=len(out.forms), blocked_requests=len(blocked))
            except Exception as e:  # noqa: BLE001
                out.errors.append(f"browser failed: {type(e).__name__}: {e}")
                self.state.log("browser_error", url=url, error=str(e))
            finally:
                await browser.close()
        self.state.forms.extend(out.forms)
        if blocked:
            out.warnings.append(f"{len(blocked)} non-GET/non-public requests blocked")
        return out
