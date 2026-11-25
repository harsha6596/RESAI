# browser-check
---
name: browser-check
description: Verify an RDR incident in the RDR Support Console UI. Log in, filter by VIN, dealer or RDR ID, and read the status, retry count and last error the UI shows.
---

# browser-check

## When to use
The orchestrator asks what the RDR dashboard shows for a VIN, dealer ID or RDR ID.

## Steps (use the `run` tool, one call per step)
1. Open the login page:
   The application primes the active page at the login URL before the agent starts. Capture exactly one fresh snapshot. Do not use action IDs from an older snapshot. If navigation is needed, use:
   `await page.goto("<RDR login URL>", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#login-form");`
2. Log in with the email and password from the system prompt. Prefer one `run` call with these stable locators rather than snapshot action IDs:
   `await page.locator("#email").fill("<email>"); await page.locator("#password").fill("<password>"); await page.locator("button.login-button").click(); await page.waitForSelector(".topbar");`
3. Filter by exactly one transaction identifier. Prefer VIN. The incident ID (`INC-...`) is not an RDR ID and must never be entered in `#filter-rdr`. Use only the field you need and prefer stable locators in one `run` call:
   `await page.locator("#filter-vin").fill("<VIN>"); await page.locator(".search-button").click();`
   Other fields: `#filter-dealer`, `#filter-rdr`. Status is a dropdown `#filter-status`.
4. Read the result. Return JSON from one `run` call:
   `const rows = await page.locator("#transaction-rows tr").evaluateAll(trs => trs.map(tr => [...tr.cells].map(td => td.innerText.trim())));`
   `const count = await page.locator("#result-count").innerText(); return { count, rows };`
   Columns, in order: checkbox, RDR ID, VIN, Dealer ID, Customer, Submitted, Status, Priority, Retry Count, Last Updated, Last Error.
5. Take one screenshot as evidence: `type: "jpeg", quality: 40, fullPage: false`.

## Output
- URL and observation time
- Result: MATCH | NOT_FOUND | MULTIPLE (count from `#result-count`)
- For each matching row: RDR ID, VIN, Dealer ID, Status, Retry Count, Last Updated, Last Error
- Anything the UI does not show: say so. Do not infer a cause.

## Example (INC-001)
Filter VIN `4T1R11AK6TA900101`. Expected from `grid_data/rdr_grid_static_data.json`: RDR `RDR-20261123-008421`, Dealer `DLR-1042`, Status `PENDING`, Retry 3, Last Error `Processing delayed`.
