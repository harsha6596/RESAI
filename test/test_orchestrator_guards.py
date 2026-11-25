"""Unit tests for the Layer 2 delegation blackboard and hard budgets."""
from types import SimpleNamespace
import unittest
import os
import time

from langchain_core.messages import ToolMessage

from app.layer2_harness import orchestrator
from app.layer2_harness import adaptive
from app.layer2_harness.subagents.browser_agent import BrowserToolLimitMiddleware
from app.schemas import Incident, InvestigationPlan, InvestigationRequest


def request(target: str, description: str, *, messages=None):
    return SimpleNamespace(
        tool_call={
            "id": f"call-{target}",
            "name": "task",
            "args": {"subagent_type": target, "description": description},
        },
        state={"messages": messages or []},
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "guard-test"}}),
    )


class DelegationGuardTests(unittest.TestCase):
    def setUp(self):
        orchestrator._BLACKBOARDS.clear()
        self.guard = orchestrator.DelegationGuardMiddleware()

    def test_same_incident_reuses_result_even_when_wording_changes(self):
        first = request("docs-agent", "Find guidance for INC-002 and VIN 4T1R11AK8TA900102")
        result = self.guard._handle(first, lambda _: ToolMessage(content="docs report", tool_call_id="one"))
        self.assertEqual(result.content, "docs report")

        second = request("docs-agent", "Check known rejection guidance for INC-002 and SUB-20261123-000237")
        reused = self.guard._handle(second, lambda _: self.fail("duplicate task executed"))
        self.assertIn("BLACKBOARD_REUSE", reused.content)
        self.assertIn("docs report", reused.content)

    def test_each_specialist_has_one_delegation_budget(self):
        messages = []
        for target in ("logs-agent", "db-agent", "browser-agent", "docs-agent"):
            call = request(target, f"Investigate INC-003 with {target}")
            self.guard._handle(call, lambda _: ToolMessage(content="report", tool_call_id="one"))
            messages.append(SimpleNamespace(tool_calls=[call.tool_call]))

        extra = request("logs-agent", "Investigate INC-003 again", messages=messages)
        blocked = self.guard._handle(extra, lambda _: self.fail("fifth task executed"))
        self.assertIn("DELEGATION_LIMIT_REACHED", blocked.content)

    def test_nullable_investigation_plan_fields_are_normalized(self):
        plan = InvestigationPlan.model_validate({
            "decision": "investigate",
            "root_cause": None,
            "recommended_action": None,
            "investigations": None,
            "pending_checks": None,
        })
        self.assertEqual(plan.root_cause, "Evidence collection has not completed.")
        self.assertEqual(plan.recommended_action, "Continue the required investigation.")
        self.assertEqual(plan.investigations, [])
        self.assertEqual(plan.pending_checks, [])


class AdaptiveExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execution_deduplicates_and_runs_independent_tasks(self):
        incident = Incident(
            incident_id="INC-TEST",
            title="RDR test",
            application="RDR",
            vin="4T1R11AK8TA900102",
            incident_description="submission rejected",
            support_request="investigate",
        )
        plan = InvestigationPlan(
            decision="investigate",
            investigations=[
                InvestigationRequest(agent="logs-agent", purpose="check logs"),
                InvestigationRequest(agent="logs-agent", purpose="same check"),
                InvestigationRequest(agent="db-agent", purpose="check record"),
            ],
        )
        calls = []
        original = adaptive._run_one

        async def fake_run(ticket, request, round_number):
            agent = request.agent if hasattr(request, "agent") else request["agent"]
            calls.append(agent)
            return agent, {"status": "completed", "report": {"result": "MATCH"}}

        adaptive._run_one = fake_run
        try:
            result = await adaptive.execute(incident, {"investigation_plan": plan})
        finally:
            adaptive._run_one = original
        self.assertEqual(sorted(calls), ["db-agent", "logs-agent"])
        self.assertEqual(result["investigation_round"], 1)
        self.assertEqual(len(result["completed_investigations"]), 2)

    async def test_browser_demo_delay_is_applied_by_agent_middleware(self):
        old = os.environ.get("BROWSER_DEMO_DELAY_SECONDS")
        os.environ["BROWSER_DEMO_DELAY_SECONDS"] = "0.03"
        try:
            middleware = BrowserToolLimitMiddleware()
            request = SimpleNamespace(
                tool_call={"id": "run-1", "name": "run", "args": {"code": "return 1"}},
                state={"messages": []},
            )

            async def handler(_):
                return "ok"

            started = time.perf_counter()
            result = await middleware.awrap_tool_call(request, handler)
            elapsed = time.perf_counter() - started
            self.assertEqual(result, "ok")
            self.assertGreaterEqual(elapsed, 0.03)
        finally:
            if old is None:
                os.environ.pop("BROWSER_DEMO_DELAY_SECONDS", None)
            else:
                os.environ["BROWSER_DEMO_DELAY_SECONDS"] = old

    async def test_browser_requires_login_and_allows_only_one_lookup(self):
        middleware = BrowserToolLimitMiddleware()
        calls = []

        async def handler(_):
            calls.append(True)
            return "ok"

        def run(code, call_id):
            return SimpleNamespace(
                tool_call={"id": call_id, "name": "run", "args": {"code": code}},
                state={"messages": []},
            )

        blocked = await middleware.awrap_tool_call(
            run('await page.locator("#filter-vin").fill("VIN"); await page.locator(".search-button").click();', "lookup-before-login"),
            handler,
        )
        self.assertIn("LOGIN_REQUIRED", blocked.content)
        self.assertEqual(calls, [])

        await middleware.awrap_tool_call(
            run('await page.locator("#email").fill("support"); await page.locator("#password").fill("secret"); await page.locator("button.login-button").click();', "login"),
            handler,
        )
        await middleware.awrap_tool_call(
            run('await page.locator("#filter-vin").fill("VIN"); await page.locator(".search-button").click();', "lookup"),
            handler,
        )
        duplicate = await middleware.awrap_tool_call(
            run('await page.locator("#filter-dealer").fill("DLR"); await page.locator(".search-button").click();', "duplicate"),
            handler,
        )
        self.assertIn("UI_LOOKUP_LIMIT_REACHED", duplicate.content)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
