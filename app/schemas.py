"""Pydantic schemas: incident input, subagent reports, and the orchestrator RCA.

Incident fields follow the ticket layout in test/RDR_incidents.txt. Report fields follow the
Output Format / Output sections in context/subagents/*/AGENTS.md and context/skills/*/SKILL.md.
"""
import json
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator


class RdrStatus(str, Enum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


# ---- Orchestrator input -----------------------------------------------------

class Incident(BaseModel):
    incident_id: str                      # "INC-001"
    title: str                            # "RDR Transaction Stuck in Pending Status"
    application: str                      # "RDR"
    incident_date: str | None = None
    vin: str | None = None
    dealer_id: str | None = None
    rdr_id: str | None = None
    submission_id: str | None = None
    reported_status: RdrStatus | None = None
    incident_description: str
    support_request: str

    def as_text(self) -> str:
        lines = [f"Incident {self.incident_id} ({self.application}): {self.title}"]
        fields = [
            ("Incident date", self.incident_date),
            ("VIN", self.vin),
            ("Dealer ID", self.dealer_id),
            ("RDR ID", self.rdr_id),
            ("Submission ID", self.submission_id),
            ("Reported status", self.reported_status.value if self.reported_status else None),
        ]
        lines += [f"{label}: {value}" for label, value in fields if value]
        lines += [f"Description: {self.incident_description}", f"Support request: {self.support_request}"]
        return "\n".join(lines)


# ---- Subagent reports (used as response_format) -----------------------------

def _as_text(value):
    """Models sometimes return structured items where a sentence is expected. Keep them as JSON text."""
    return value if isinstance(value, str) else json.dumps(value, default=str)


Text = Annotated[str, BeforeValidator(_as_text)]


class LogsReport(BaseModel):
    application_workflow: Text = "not stated"
    issue_symptom: Text
    observed_status: Text | None = None
    classification: Literal[
        "application", "validation/input", "upstream dependency", "downstream dependency",
        "infrastructure/database", "authentication/authorization", "performance", "unknown",
    ]
    root_cause_status: Literal["confirmed", "probable", "undetermined", "insufficient_evidence"]
    root_cause: Text
    evidence: list[Text] = Field(default_factory=list)  # file, timestamp, service, event, trace.id, line
    timeline: list[Text] = Field(default_factory=list)
    impact_scope: Text = "not stated"
    recommended_next_action: Text = "not stated"
    other_agent_checks: list[Text] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"


class DbDiagnosis(BaseModel):
    path: Literal["A_INVESTIGATE", "B_CORRECT"]   # A: why stuck. B: ticket asks for a fix.
    classification: Literal["VERIFIED", "PROPOSE_WRITE", "ESCALATE"]
    record_found: bool
    findings: list[str]                           # short facts only: status, transaction_state, retry_count, last_error, row_version
    write_result: Literal["APPLIED", "NOT_APPLIED", "REJECTED"] | None = None


class DocFinding(BaseModel):
    label: Literal["RELEVANT", "POSSIBLY_RELEVANT", "NOT_RELEVANT"]
    citation: str                         # "file#chunk"
    reason: str
    suggested_step: str | None = None


class DocsReport(BaseModel):
    queries: list[str]
    findings: list[DocFinding]
    not_covered: list[str]
    other_agent_checks: list[str]
    result: Literal["FOUND", "NO_MATCH", "INDEX_NOT_BUILT"]


class BrowserRow(BaseModel):
    # Rejected/blocked submissions may not have an RDR ID because no
    # transaction was created yet.
    rdr_id: str | None = None
    vin: str
    dealer_id: str
    status: str
    retry_count: int
    last_updated: str
    last_error: str | None = None


class BrowserReport(BaseModel):
    url: str
    observed_at: str
    result: Literal["MATCH", "NOT_FOUND", "MULTIPLE"]
    rows: list[BrowserRow]
    not_shown: list[str]
    screenshot: str | None = None  # file name under data/screenshots/, set by Python


# ---- Orchestrator output ----------------------------------------------------

class Rca(BaseModel):
    # The model occasionally omits this echo of the ticket identifier even
    # though it is present in the prompt. The orchestrator fills it from the
    # trusted Incident object before exposing the RCA to the rest of the app.
    incident_id: str | None = None
    root_cause_status: Literal["confirmed", "probable", "undetermined"] = "undetermined"
    root_cause: str = "The available evidence is insufficient to determine the root cause."
    recommended_action: str = "Escalate for additional investigation."
    pending_checks: list[str] = Field(default_factory=list)  # DB / docs / logs / browser checks still needed

    @field_validator("root_cause_status", mode="before")
    @classmethod
    def normalize_rca_status(cls, value):
        return value if value in {"confirmed", "probable", "undetermined"} else "undetermined"

    @field_validator("root_cause", mode="before")
    @classmethod
    def normalize_rca_root_cause(cls, value):
        return value if isinstance(value, str) and value.strip() else "The available evidence is insufficient to determine the root cause."

    @field_validator("recommended_action", mode="before")
    @classmethod
    def normalize_rca_action(cls, value):
        return value if isinstance(value, str) and value.strip() else "Escalate for additional investigation."

    @field_validator("pending_checks", mode="before")
    @classmethod
    def normalize_rca_pending_checks(cls, value):
        return [] if value is None else value


class InvestigationRequest(BaseModel):
    """One bounded specialist request emitted by the Layer 2 orchestrator."""

    agent: Literal["logs-agent", "db-agent", "docs-agent", "browser-agent"]
    purpose: str = "Collect the required specialist evidence."
    scope: str = "initial"

    @field_validator("purpose", mode="before")
    @classmethod
    def normalize_purpose(cls, value):
        return value if isinstance(value, str) and value.strip() else "Collect the required specialist evidence."

    @field_validator("scope", mode="before")
    @classmethod
    def normalize_scope(cls, value):
        return value if isinstance(value, str) and value.strip() else "initial"


class WriteRequest(BaseModel):
    """Optional guarded correction proposed for the resolution phase."""

    vin: str
    field: Literal["customer_name", "sale_type", "delivery_date", "dealer_id"]
    new_value: str | None = None
    expected_old_value: str | None = None
    expected_row_version: int | None = None
    reason: str


class InvestigationPlan(BaseModel):
    """Structured decision shared by planning, analysis and replanning."""

    decision: Literal["investigate", "resolve", "escalate"]
    investigations: list[InvestigationRequest] = Field(default_factory=list)
    root_cause_status: Literal["confirmed", "probable", "undetermined"] = "undetermined"
    root_cause: str = "Evidence collection has not completed."
    recommended_action: str = "Continue the required investigation."
    pending_checks: list[str] = Field(default_factory=list)
    write_request: WriteRequest | None = None

    @field_validator("root_cause_status", mode="before")
    @classmethod
    def normalize_plan_status(cls, value):
        # The model sometimes answers "insufficient_evidence" (a LogsReport value). Treat any
        # unknown value as undetermined instead of failing the whole run.
        return value if value in {"confirmed", "probable", "undetermined"} else "undetermined"

    @field_validator("investigations", "pending_checks", mode="before")
    @classmethod
    def normalize_lists(cls, value):
        return [] if value is None else value

    @field_validator("root_cause", mode="before")
    @classmethod
    def normalize_root_cause(cls, value):
        return value if isinstance(value, str) and value.strip() else "Evidence collection has not completed."

    @field_validator("recommended_action", mode="before")
    @classmethod
    def normalize_recommended_action(cls, value):
        return value if isinstance(value, str) and value.strip() else "Continue the required investigation."


# ---- Layer 1 output ---------------------------------------------------------

class Layer1Result(BaseModel):
    resolved: bool                        # False hands the incident to Layer 2
    answer: str
    sources: list[str] = Field(default_factory=list)  # "category/file#chunk"; the model omits it when unresolved
