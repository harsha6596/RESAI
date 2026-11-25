"""Manual Layer 2 check: run the DeepAgents orchestrator on one live ticket, with approval prompts.

Run: python -m app.layer2_harness.test_orchestrator
"""
import asyncio
import uuid

from app.layer2_harness.orchestrator import investigate, resume_investigation
from app.schemas import Incident, RdrStatus


async def main():
    incident = Incident(
        incident_id="INC-001",
        title="RDR Transaction Stuck in Pending Status",
        application="RDR",
        vin="4T1R11AK6TA900101",
        dealer_id="DLR-1042",
        rdr_id="RDR-20261123-008421",
        submission_id="SUB-20261123-000184",
        reported_status=RdrStatus.PENDING,
        incident_description="RDR remained in PENDING status. Dashboard displays Processing delayed.",
        support_request="Investigate why the RDR is still Pending and recommend a resolution.",
    )
    thread = str(uuid.uuid4())
    result = await investigate(incident, thread)

    while "__interrupt__" in result:
        print("\n[PAUSED FOR HUMAN APPROVAL]")
        print(result["__interrupt__"][0].value)
        choice = input("\nApprove action? (y/n): ").strip().lower()
        decision = (
            {"decisions": [{"type": "approve"}]}
            if choice == "y"
            else {"decisions": [{"type": "reject", "message": "Action denied by engineer."}]}
        )
        result = await resume_investigation(thread, decision, incident.incident_id)

    print("\n--- Layer 2 RCA ---")
    print(result["structured_response"].model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
