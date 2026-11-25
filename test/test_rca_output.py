import unittest

from app.layer2_harness.orchestrator import _normalize_rca
from app.schemas import Rca


class RcaOutputTests(unittest.TestCase):
    def _rca(self, incident_id=None, status="undetermined"):
        payload = {
            "root_cause_status": status,
            "root_cause": "The available evidence is inconclusive.",
            "recommended_action": "Collect the missing evidence.",
            "pending_checks": ["Review the submission logs."],
        }
        if incident_id is not None:
            payload["incident_id"] = incident_id
        return Rca.model_validate(payload)

    def test_missing_incident_id_is_restored_from_ticket(self):
        # Exact failure shape returned by the model in the reported incident.
        result = _normalize_rca({"structured_response": self._rca()}, "INC-002")
        self.assertEqual(result["structured_response"].incident_id, "INC-002")

    def test_blank_incident_id_is_restored(self):
        result = _normalize_rca({"structured_response": self._rca("")}, "INC-002")
        self.assertEqual(result["structured_response"].incident_id, "INC-002")

    def test_matching_incident_id_is_preserved(self):
        result = _normalize_rca({"structured_response": self._rca("INC-002")}, "INC-002")
        self.assertEqual(result["structured_response"].incident_id, "INC-002")

    def test_conflicting_incident_id_is_replaced_by_ticket_id(self):
        result = _normalize_rca({"structured_response": self._rca("INC-MODEL")}, "INC-002")
        self.assertEqual(result["structured_response"].incident_id, "INC-002")

    def test_interrupt_result_is_unchanged(self):
        result = {"__interrupt__": [{"value": {"action_requests": []}}]}
        self.assertIs(_normalize_rca(result, "INC-002"), result)

    def test_non_rca_result_is_unchanged(self):
        result = {"structured_response": {"root_cause": "not parsed yet"}}
        self.assertIs(_normalize_rca(result, "INC-002"), result)

    def test_all_supported_statuses_validate(self):
        for status in ("confirmed", "probable", "undetermined"):
            with self.subTest(status=status):
                self.assertEqual(self._rca(status=status).root_cause_status, status)

    def test_nullable_rca_fields_get_safe_defaults(self):
        result = Rca.model_validate({"root_cause_status": "undetermined"})
        self.assertIn("insufficient", result.root_cause)
        self.assertEqual(result.pending_checks, [])


if __name__ == "__main__":
    unittest.main()
