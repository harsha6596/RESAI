"""Parse a pasted incident ticket (the layout used in test/RDR_incidents.txt) into an Incident."""
import re
from datetime import datetime
from uuid import uuid4

from pydantic import ValidationError

from app.schemas import Incident

HEADER = re.compile(r"^\s*(INC-[\w-]+)\s*[—–-]\s*(.+?)\s*$", re.M)
FIELD = re.compile(r"^\s*(Application|VIN|Dealer ID|RDR ID|Submission ID|Reported Status|Incident Date):[ \t]*(.+?)\s*$", re.M)
DESCRIPTION = re.compile(r"Incident Description:\s*(.*?)(?=Support Request:|\Z)", re.S)
REQUEST = re.compile(r"Support Request:\s*(.*)", re.S)


def _drop_separators(text: str) -> str:
    """Remove lines made only of '=' or '-' (the ticket file uses them as dividers)."""
    return "\n".join(
        line for line in text.splitlines()
        if not (line.strip() and set(line.strip()) <= {"=", "-"})
    )


def parse_ticket(text: str) -> Incident:
    """Raise ValueError with a readable message if the ticket is missing a required part."""
    text = _drop_separators(text)
    header = HEADER.search(text)
    stamp = uuid4().hex[:6]  # unique per submission, so a repeated ticket does not collide
    if header:
        incident_id, title = f"{header.group(1)}-{stamp}", header.group(2)
    else:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        incident_id, title = f"INC-{datetime.now():%Y%m%d%H%M%S}-{stamp}", first[:120]
    if not title:
        raise ValueError("The ticket is empty.")

    fields = dict(FIELD.findall(text))
    description = DESCRIPTION.search(text)
    request = REQUEST.search(text)
    if not description or not description.group(1).strip():
        raise ValueError("Missing 'Incident Description:' section.")
    if not request or not request.group(1).strip():
        raise ValueError("Missing 'Support Request:' section.")

    status = fields.get("Reported Status")
    try:
        return Incident(
            incident_id=incident_id,
            title=title,
            application=fields.get("Application", "RDR").split(" (")[0],
            incident_date=fields.get("Incident Date"),
            vin=fields.get("VIN"),
            dealer_id=fields.get("Dealer ID"),
            rdr_id=fields.get("RDR ID"),
            submission_id=fields.get("Submission ID"),
            reported_status=status,
            incident_description=" ".join(description.group(1).split()),
            support_request=" ".join(request.group(1).split()),
        )
    except ValidationError:
        raise ValueError(f"Unknown reported status: {status}. Use one of PENDING, REJECTED, BLOCKED, COMPLETED, FAILED, RETRYING, CANCELLED.")
