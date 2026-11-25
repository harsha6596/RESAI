""" 
browser_agent Subagent: This agent logs into the RDR support console and reads the incident state. Read only
"""

import asyncio
import os 
import re
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv() 
from pathlib import Path 
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_stagehand import StagehandMiddleware
from stagehand import Stagehand, local_browser

from app.schemas import BrowserReport, BrowserRow

ROOT = Path(__file__).resolve().parents[3]  # repo root
# Use the same loopback address that start.py probes.  This avoids resolving
# localhost to IPv6 (::1) in a browser while the demo server is reachable over
# IPv4 only.
RDR_URL = "http://127.0.0.1:8765/web/"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _system_prompt() -> str:
    login = (
        f"RDR login page: {RDR_URL}\n"
        f"Login email: {os.environ['RDR_EMAIL']}\n"
        f"Login password: {os.environ['RDR_PASSWORD']}\n"
        "Use these only on the RDR login page. Never repeat the password in your report."
    )
    demo = ""
    if BrowserToolLimitMiddleware._demo_delay():
        demo = (
            "DEMONSTRATION MODE: keep the browser visibly slow and understandable. Use separate "
            "run calls for login, filtering, extracting the row, and screenshot. Do not bundle "
            "multiple phases into one run call. Pause naturally between phases and stop after the result."
        )
    return "\n\n".join([
        _read("context/subagents/browser-agent/AGENTS.md"),
        _read("context/skills/browser-check/SKILL.md"),
        (
            "IMPORTANT EXECUTION RULE: use the Stagehand run tool with `code` and stable CSS "
            "locators for all interactions. Do not send `actions` with guessed semantic IDs such "
            "as email, submit, or filter-vin. The active page is already the RDR console; do not "
            "call page.goto or navigate again. If .topbar is already present, reuse the logged-in "
            "dashboard and skip login. Otherwise use the primed login page and make the first run "
            "login-only using #email, #password and button.login-button. Complete login before any "
            "filter or search call. "
            "Use exactly one transaction identifier for the UI lookup: prefer VIN, then dealer ID, "
            "then an actual RDR ID. NEVER put the incident ID (INC-...) or submission ID into the "
            "RDR ID filter. Do not repeat a lookup with alternate identifiers after a MATCH or "
            "NOT_FOUND result. Take at most one screenshot, after extracting the row, then return "
            "the BrowserReport immediately."
        ),
        demo,
        login,
    ])


class BrowserToolLimitMiddleware(AgentMiddleware):
    """Bound browser calls so screenshots/navigation cannot create token runaway."""

    MAX_RUNS = 6
    MAX_SNAPSHOTS = 1
    MAX_SCREENSHOTS = 1

    def __init__(self):
        super().__init__()
        self._login_completed = False
        self._lookup_completed = False

    @staticmethod
    def _demo_delay() -> float:
        """Optional visual pause for demonstrations; production default is immediate."""
        try:
            return max(0.0, min(float(os.getenv("BROWSER_DEMO_DELAY_SECONDS", "0")), 10.0))
        except ValueError:
            return 0.0

    def _counts(self, state) -> dict[str, int]:
        counts = {"run": 0, "snapshot": 0, "screenshot": 0}
        for message in state.get("messages", []):
            for call in getattr(message, "tool_calls", None) or []:
                if call.get("name") in counts:
                    counts[call["name"]] += 1
        return counts

    def _blocked(self, request, name: str, limit: int):
        return ToolMessage(
            content=(
                f"{name.upper()}_LIMIT_REACHED: browser-agent may call {name} at most {limit} "
                "time(s). Stop interacting and return the BrowserReport from the evidence already collected."
            ),
            tool_call_id=request.tool_call["id"],
            name=name,
        )

    def _check(self, request):
        name = request.tool_call["name"]
        limits = {
            "run": self.MAX_RUNS,
            "snapshot": self.MAX_SNAPSHOTS,
            "screenshot": self.MAX_SCREENSHOTS,
        }
        if name in limits and self._counts(request.state)[name] >= limits[name]:
            return self._blocked(request, name, limits[name])
        if name == "run":
            code = (request.tool_call.get("args") or {}).get("code") or ""
            lowered = code.lower()
            login_step = "#email" in lowered and "#password" in lowered and "login-button" in lowered
            lookup_step = "filter-vin" in lowered or "filter-dealer" in lowered or "filter-rdr" in lowered
            if self._demo_delay() and login_step and lookup_step:
                return ToolMessage(
                    content=(
                        "DEMO_STEP_REQUIRED: perform login in this run only. Wait for the visible login "
                        "to finish, then use a separate run for the transaction lookup."
                    ),
                    tool_call_id=request.tool_call["id"],
                    name="run",
                )
            if lookup_step and not self._login_completed:
                return ToolMessage(
                    content=(
                        "LOGIN_REQUIRED: the RDR login has not completed in this browser session. "
                        "Use one run with #email, #password and button.login-button first."
                    ),
                    tool_call_id=request.tool_call["id"],
                    name="run",
                )
            if lookup_step and self._lookup_completed:
                return ToolMessage(
                    content=(
                        "UI_LOOKUP_LIMIT_REACHED: one transaction lookup is allowed per browser session. "
                        "Use the existing result and return BrowserReport; do not try dealer or RDR filters."
                    ),
                    tool_call_id=request.tool_call["id"],
                    name="run",
                )
            if "page.goto" in code:
                return ToolMessage(
                    content=(
                        "NAVIGATION_BLOCKED: the active RDR console page is already prepared. "
                        "Do not navigate or retry with a URL. Use stable CSS locators on the current page."
                    ),
                    tool_call_id=request.tool_call["id"],
                    name="run",
                )
        return None

    def wrap_tool_call(self, request, handler):
        blocked = self._check(request)
        if blocked is not None:
            return blocked
        result = handler(request)
        self._record_browser_progress(request)
        delay = self._demo_delay()
        if delay:
            time.sleep(delay)
        return result

    async def awrap_tool_call(self, request, handler):
        blocked = self._check(request)
        if blocked is not None:
            return blocked
        result = await handler(request)
        self._record_browser_progress(request)
        delay = self._demo_delay()
        if delay:
            await asyncio.sleep(delay)
        return result

    def _record_browser_progress(self, request):
        if request.tool_call["name"] != "run":
            return
        code = (request.tool_call.get("args") or {}).get("code") or ""
        lowered = code.lower()
        if (
            ("#email" in lowered and "#password" in lowered and "login-button" in lowered)
            or (".topbar" in lowered and ("count()" in lowered or "isvisible" in lowered))
        ):
            self._login_completed = True
        if any(selector in lowered for selector in ("filter-vin", "filter-dealer", "filter-rdr")):
            self._lookup_completed = True

async def create_browser_subagent():
    """Returns (subagent_spec, browser). Caller must `await browser.close()`."""
    # Always headful: the demo and support workflow must show the actual
    # browser session rather than silently running in the background.
    browser = await local_browser.launch(headless=False)
    try:
        stagehand = await Stagehand.create(browser=browser)
        # local_browser starts with a blank active page.  Prime it before the
        # agent receives the Stagehand tools; otherwise the model can try to
        # act on snapshot IDs from the wrong page and repeatedly retry login.
        page = await browser.context.active_page()
        if page is None:
            page = await browser.context.new_page()
            await browser.context.set_active_page(page)
        await page.goto(RDR_URL, wait_until="domcontentloaded")
        await page.wait_for_selector("#login-form")
    except Exception:
        await browser.close()
        raise

    middleware = StagehandMiddleware(stagehand=stagehand)
    # Hydrate the facade's snapshot cache before the model receives the tools.
    # This makes the browser usable even if a model incorrectly attempts one
    # snapshot-action call before following the CSS-locator instruction.
    await middleware.tools[1].ainvoke({"includeIframes": True})

    subagent = {
        "name": "browser-agent",
        "description": "Logs into the RDR Support Console and reads transaction status, last error and retry count for a VIN, dealer or RDR ID. Read-only.",
        "system_prompt": _system_prompt(),
        "response_format": BrowserReport,
        "middleware": [BrowserToolLimitMiddleware(), middleware],
    }
    return subagent, browser


_ROWS_JS = """(() => [...document.querySelectorAll('#transaction-rows tr')].map(tr =>
  [...tr.cells].map(td => td.innerText.trim())))()"""


def _dash(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value in ("", "—", "-") else value


SCREENSHOT_DIR = ROOT / "data" / "screenshots"


async def read_ui_row(vin: str | None, rdr_id: str | None, dealer_id: str | None, incident_id: str | None = None) -> BrowserReport:
    """Log in, filter the grid and read the row with fixed steps (no model in the loop).

    The model-driven run sometimes skipped the Search click, filtered by the wrong field or ran out
    of steps, so the row looked missing. Fixed steps are repeatable, and the pause between them
    (BROWSER_STEP_DELAY_SECONDS, default 0.8) keeps login, typing and the filtered grid watchable.
    """
    if vin:
        field, value = "#filter-vin", vin
    elif rdr_id:
        field, value = "#filter-rdr", rdr_id
    elif dealer_id:
        field, value = "#filter-dealer", dealer_id
    else:
        raise ValueError("No VIN, RDR ID or dealer ID to look up")
    try:
        pause_ms = int(max(0.0, min(float(os.getenv("BROWSER_STEP_DELAY_SECONDS", "0.8")), 10.0)) * 1000)
    except ValueError:
        pause_ms = 800

    browser = await local_browser.launch(headless=False)
    try:
        await Stagehand.create(browser=browser)
        page = await browser.context.active_page()
        if page is None:
            page = await browser.context.new_page()
            await browser.context.set_active_page(page)
        await page.goto(RDR_URL, wait_until="domcontentloaded")
        await page.wait_for_selector("#login-form", timeout=15000)
        await page.wait_for_timeout(pause_ms)

        # Login (skipped if a session is already open).
        if await page.locator(".topbar").count() == 0:
            await page.locator("#email").fill(os.environ["RDR_EMAIL"])
            await page.wait_for_timeout(pause_ms // 2)
            await page.locator("#password").fill(os.environ["RDR_PASSWORD"])
            await page.wait_for_timeout(pause_ms // 2)
            await page.locator("button.login-button").click()
            await page.wait_for_selector(".topbar", timeout=15000)
            await page.wait_for_timeout(pause_ms)

        # Search: fill the one identifier, then press Search (the grid also filters as you type).
        await page.wait_for_selector("#transaction-rows tr", timeout=15000)
        await page.locator(field).fill(value)
        await page.wait_for_timeout(pause_ms // 2)
        await page.locator("button.search-button").click()
        await page.wait_for_timeout(pause_ms)

        rows = await page.evaluate(_ROWS_JS) or []
        count_text = await page.locator("#result-count").inner_text()
        # Evidence image: taken after login, so the password is never on screen.
        shot_name = None
        if incident_id:
            try:
                SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                shot_name = f"{re.sub(r'[^A-Za-z0-9_-]', '_', incident_id)}.jpg"
                await page.screenshot(path=str(SCREENSHOT_DIR / shot_name), type="jpeg", quality=60)
            except Exception:
                shot_name = None  # a failed capture must not fail the check
        await page.wait_for_timeout(pause_ms)  # leave the filtered grid on screen before closing
    finally:
        await browser.close()

    data = [r for r in rows if len(r) >= 11]  # the "no match" row has a single cell
    report_rows = [
        BrowserRow(
            rdr_id=_dash(r[1]), vin=r[2], dealer_id=r[3], status=r[6],
            retry_count=int(r[8] or 0), last_updated=r[9], last_error=_dash(r[10]),
        )
        for r in data
    ]
    result = "NOT_FOUND" if not report_rows else ("MULTIPLE" if len(report_rows) > 1 else "MATCH")
    return BrowserReport(
        url=RDR_URL,
        observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        result=result,
        rows=report_rows,
        screenshot=shot_name,
        not_shown=[f"Customer, priority and submitted time were not reported. Grid said: {count_text}"],
    )
