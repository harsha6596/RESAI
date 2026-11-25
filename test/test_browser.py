import asyncio
import json
import sys
from pathlib import Path

# Running `python test/test_browser.py` puts test/ on sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from app.layer2_harness.subagents.browser_agent import create_browser_subagent  # noqa: E402
from app.schemas import BrowserReport  # noqa: E402

ALERT = (
    "Use browser-agent only. Verify in the RDR Support Console UI: VIN 4T1R11AK8TA900102, "
    "Dealer DLR-2077, Submission ID SUB-20261123-000237. Report the status and last error "
    "the UI shows. The RDR ID may be absent because the submission was rejected. "
    "Do not diagnose the cause."
)


async def main():
    browser_subagent, browser = await create_browser_subagent()
    try:
        # Exercise the browser-agent's exact read-only workflow without a
        # second supervisor model changing the task or adding tool calls.
        # BrowserToolLimitMiddleware wraps the Stagehand middleware but does not
        # own the browser tools. Select the Stagehand middleware explicitly so
        # this deterministic test remains stable when guards are reordered.
        middleware = next(item for item in browser_subagent["middleware"] if hasattr(item, "tools"))
        run = middleware.tools[0]
        await run.ainvoke({"code": 'await page.locator("#email").fill("support@rdr.company.com"); await page.locator("#password").fill("RDR-Support-2026!"); await page.locator("button.login-button").click(); await page.waitForSelector(".topbar");', "actions": None})
        result = await run.ainvoke({"code": 'await page.locator("#filter-vin").fill("4T1R11AK8TA900102"); await page.locator(".search-button").click(); const rows = await page.locator("#transaction-rows tr").evaluateAll(trs => trs.map(tr => [...tr.cells].map(td => td.innerText.trim()))); const count = await page.locator("#result-count").innerText(); return { count, rows };', "actions": None})
        data = result if isinstance(result, dict) else json.loads(result)
        row = data["rows"][0]
        report = BrowserReport(
            url="http://127.0.0.1:8765/web/",
            observed_at="test",
            result="MATCH",
            rows=[{
                "rdr_id": None if row[1] == "—" else row[1],
                "vin": row[2],
                "dealer_id": row[3],
                "status": row[6],
                "retry_count": int(row[8]),
                "last_updated": row[9],
                "last_error": row[10],
            }],
            not_shown=[],
        )
        assert report.result == "MATCH"
        assert report.rows[0].vin == "4T1R11AK8TA900102"
        assert report.rows[0].status == "REJECTED"
        await middleware.tools[2].ainvoke({"fullPage": False, "type": "jpeg", "quality": 40})
        print(report.model_dump_json(indent=2))
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
