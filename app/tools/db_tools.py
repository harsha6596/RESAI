"""SQLite tools for the db-agent. DB path comes from INCIDENT_DB_PATH only (not model-controlled).

Python-only (never exposed to the LLM):
  extract_ids, prefetch_record: regex ID extraction and a parameterized read of the matching row.
LLM tools:
  query_readonly: fallback single SELECT when the ticket has no IDs (mode=ro).
  update_rdr_record: guarded single-row UPDATE, audit-logged. Reachable only through the
  db-agent's HITL interrupt, so it runs only after human approval.
"""

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

_READ_ONLY_SQL = re.compile(r"^\s*(select|with)\b(?!.*\b(insert|update|delete|drop|alter|create|pragma|attach)\b)", re.I | re.S)

# ID formats from test/RDR_incidents.txt. VIN is 17 chars, no I/O/Q.
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_RDR_ID = re.compile(r"\bRDR-\d{8}-\d{6}\b")
_SUB_ID = re.compile(r"\bSUB-\d{8}-\d{6}\b")

# Columns the LLM may see. Explicit list, so SELECT * is never used.
_RECORD_COLUMNS = (
    "rdr_id, vin, dealer_id, submission_id, customer_name, sale_type, delivery_date, "
    "status, transaction_state, retry_count, last_error, row_version, last_updated, source_type"
)

# Fields a DCR may change. VIN, submission_id, rdr_id, status, transaction_state, retry_count
# and last_error are deliberately excluded (ESCALATE, see context/subagents/db-agent/AGENTS.md).
WRITABLE_FIELDS = {"customer_name", "sale_type", "delivery_date", "dealer_id"}


def _db_path() -> Path:
    return Path(os.environ["INCIDENT_DB_PATH"]).resolve()


def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def extract_ids(text: str) -> dict[str, str]:
    """Find VIN, RDR ID and submission ID in free text. Missing IDs are left out."""
    ids = {}
    if m := _VIN.search(text):
        ids["vin"] = m.group(0)
    if m := _RDR_ID.search(text):
        ids["rdr_id"] = m.group(0)
    if m := _SUB_ID.search(text):
        ids["submission_id"] = m.group(0)
    return ids


def prefetch_record(ids: dict[str, str]) -> list[dict]:
    """Read rows matching any given ID. Values are bound, never interpolated. Returns [] when no IDs."""
    if not ids:
        return []
    clauses = [f"{col} = ?" for col in ("vin", "rdr_id", "submission_id") if col in ids]
    params = [ids[col] for col in ("vin", "rdr_id", "submission_id") if col in ids]
    sql = f"SELECT {_RECORD_COLUMNS} FROM rdr_records WHERE {' OR '.join(clauses)} LIMIT 5"
    try:
        with closing(_connect_ro()) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error as exc:
        return [{"error": f"Pre-fetch failed: {exc}"}]


def query_readonly(sql: str, max_rows: int = 20) -> list[dict]:
    """Fallback: run one SELECT against the RDR table rdr_records (read-only). Use only when the
    ticket has no VIN, RDR ID or submission ID. Returns up to 20 rows.
    Errors come back as a row with an "error" key, so the agent can correct the query."""
    max_rows = min(max_rows, 20)  # keep results small: they are re-sent on every later model call
    if not _READ_ONLY_SQL.match(sql) or ";" in sql.rstrip().rstrip(";"):
        return [{"error": "Only a single SELECT statement on rdr_records is allowed."}]
    try:
        with closing(_connect_ro()) as conn:
            return [dict(r) for r in conn.execute(sql).fetchmany(max_rows)]
    except sqlite3.Error as exc:
        return [{"error": f"SQL error: {exc}. The only table is rdr_records."}]


def update_rdr_record(
    dcr_id: str,
    vin: str,
    field: str,
    new_value: str | None,
    expected_old_value: str | None,
    expected_row_version: int,
    reason: str,
) -> dict:
    """Apply one guarded field correction to one RDR record. Only runs after HITL approval.

    The UPDATE matches the VIN, the current value and the row_version read in the pre-fetch. It must
    change exactly one row, otherwise nothing is committed. Allowed fields: customer_name, sale_type,
    delivery_date, dealer_id. Every attempt is appended to db_changes.jsonl next to the DB.
    """
    if field not in WRITABLE_FIELDS:
        return {"status": "REJECTED", "rows_affected": 0,
                "reason": f"Field '{field}' is not writable. Allowed: {sorted(WRITABLE_FIELDS)}"}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    # `field` is whitelisted above, so interpolating the column name is safe; values are bound.
    sql = (
        f"UPDATE rdr_records SET {field} = ?, row_version = row_version + 1, last_updated = ? "
        f"WHERE vin = ? AND {field} IS ? AND row_version = ?"
    )
    db = _db_path()
    with closing(sqlite3.connect(db)) as conn:
        cur = conn.execute(sql, (new_value, now, vin, expected_old_value, expected_row_version))
        rows = cur.rowcount
        if rows != 1:
            conn.rollback()
            result = {"status": "NOT_APPLIED", "rows_affected": rows,
                      "reason": "Record changed or does not match expected values. Re-read the record before reporting."}
        else:
            conn.commit()
            result = {"status": "APPLIED", "rows_affected": 1, "last_updated": now}

    audit = {"at": now, "dcr_id": dcr_id, "vin": vin, "field": field, "old": expected_old_value,
             "new": new_value, "expected_row_version": expected_row_version, "reason": reason, **result}
    with open(db.parent / "db_changes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(audit) + "\n")
    return result
