"""Deterministic local evidence smoke test for representative RDR incidents."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools.db_tools import prefetch_record
from app.tools.log_tools import investigate_logs

os.environ.setdefault("INCIDENT_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "rdr.db"))


def main() -> None:
    cases = [
        ("INC-001", "4T1R11AK6TA900101", "SUB-20261123-000184", "PENDING"),
        ("INC-002", "4T1R11AK8TA900102", "SUB-20261123-000237", "REJECTED"),
        ("INC-003", "4T1R11AKXTA900103", "SUB-20261123-000316", "BLOCKED"),
    ]
    for incident_id, vin, submission_id, expected_status in cases:
        rows = prefetch_record({"vin": vin, "submission_id": submission_id})
        assert rows and rows[0]["status"] == expected_status
        logs = investigate_logs.invoke({"identifiers": [vin, submission_id]})
        assert logs.get("files")
        print(f"{incident_id}: DB={expected_status}; log evidence={len(logs['files'])} file(s)")


if __name__ == "__main__":
    main()
