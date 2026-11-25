---
name: db-investigation
description: Verify an RDR data change request against the RDR database, apply one guarded field correction after human approval when policy allows, or escalate to a business workflow.
---

# db-investigation

## When to use
The orchestrator sends a data change request (DCR): a correction to customer name, sale type, delivery date, dealer association, status, VIN, or retry state.

## Steps
1. **Read the prefetched record.** Python has already looked it up by VIN, RDR ID or submission ID. If it says none was found, make one `query_readonly` call on `rdr_records` using the VIN. If still nothing, report NOT_FOUND.
2. **Compare to the ticket.** For each field the ticket mentions, note the current value, `row_version`, `status`, `transaction_state`, `retry_count`, `last_error`, `source_type`.
3. **Check conflicts.** If the request touches a dealer link, check the target dealer exists in the record set and no other row needs to change with it.
4. **Classify** using `AGENTS.md`: VERIFIED, PROPOSE_WRITE, or ESCALATE. Choose Path A (investigate) or Path B (correct).
5. **For PROPOSE_WRITE only (Path B)**, call `update_rdr_record` with:
   - `dcr_id`: the ticket ID (e.g. `DCR-001`)
   - `vin`: the record VIN
   - `field`: one of `customer_name`, `sale_type`, `delivery_date`, `dealer_id`
   - `new_value`: the requested value
   - `expected_old_value`: the current value you just read (`null` if NULL)
   - `expected_row_version`: the `row_version` you just read
   - `reason`: ticket ID + evidence
   The run pauses for approval. Do not call it twice for the same change.
6. **Report the result.** On `APPLIED`, quote the new value and `row_version` from the tool result. On `NOT_APPLIED`, report why.

## Guardrails
- Writes only through `update_rdr_record`. Never write raw SQL. The tool rejects other fields.
- Never change `vin`, `submission_id`, `rdr_id`, `status`, `transaction_state`, `retry_count`, `last_error` through this skill. Those are ESCALATE.
- Never move a status to COMPLETED without database evidence of recovery.
- Respect the CHECK rules: REJECTED/BLOCKED rows must keep `rdr_id` NULL and `transaction_state` NOT_CREATED. A correction that conflicts with them is ESCALATE.
- Apply corrections to `source_type = 'matched_to_provided_logs'` records only with log or document evidence stated in `reason`.

## Output
- Record found: yes/no, key columns with current values, `row_version`.
- Each ticket claim: CONFIRMED | CONTRADICTED | NOT_VERIFIABLE, with the evidence (field and value).
- Classification: VERIFIED | PROPOSE_WRITE | ESCALATE, one-line reason.
- For PROPOSE_WRITE: the approval outcome (APPLIED / NOT_APPLIED / rejected) and the post-change value.
- For ESCALATE: the business workflow that should handle it. No write attempted.
- Anything the database cannot show: say so.

## Example (DCR-001)
Ticket: VIN `4T1R11AK1TA900104`, name "Avery Collins" should be "Avery C. Collins".
1. Prefetched: `customer_name = 'Avery Collins'`, `row_version = 1`, `source_type = grid_fixture_only`.
2. Classify: PROPOSE_WRITE on `customer_name` (Path B).
3. `update_rdr_record(dcr_id="DCR-001", vin="4T1R11AK1TA900104", field="customer_name", new_value="Avery C. Collins", expected_old_value="Avery Collins", expected_row_version=1, reason="DCR-001 dealer request, identity check pending")`
4. Approval required. On APPLIED, the tool result gives `row_version` 2 for the new state.
