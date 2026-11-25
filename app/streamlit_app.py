"""Streamlit UI: paste an incident, and watch live progress, results and approvals on the dashboard.

Run the API first (uvicorn app.main:app), then: streamlit run app/streamlit_app.py
"""
import html
import os
import sys
import time
from pathlib import Path

import requests
import streamlit as st

# Streamlit puts app/ on the import path, not the repo root, so add the root for the shared parser.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ticket_parser import parse_ticket  # noqa: E402

API = os.getenv("API_URL", "http://127.0.0.1:8000")
EXAMPLE = """INC-002 — Dealer Unable to Complete RDR Submission
Application: RDR (Retail Delivery Report)
VIN: 4T1R11AK8TA900102
Dealer ID: DLR-2077
Submission ID: SUB-20261123-000237
Reported Status: REJECTED
Incident Date: November 23, 2026

Incident Description:
A dealership user reported that they are unable to submit an RDR for a recently sold vehicle.

Support Request:
Investigate why the RDR submission is being rejected and recommend the corrective action."""


def call(method: str, path: str, **kwargs):
    try:
        resp = requests.request(method, f"{API}{path}", timeout=10, **kwargs)
    except requests.RequestException as exc:
        st.error(f"API not reachable at {API}: {exc}")
        return None
    if not resp.ok:
        st.error(resp.json().get("detail", resp.text))
        return None
    return resp.json()


def submit_page():
    st.header("Submit incident")
    st.caption("Paste the whole incident ticket. The title, VIN, dealer, IDs, status, description and support request are read from it.")
    with st.form("incident", clear_on_submit=False):
        ticket = st.text_area("Incident ticket", value=EXAMPLE, height=380)
        submitted = st.form_submit_button("Submit")

    if not submitted:
        return
    try:
        incident = parse_ticket(ticket)
    except ValueError as exc:
        st.error(str(exc))
        return

    result = call("POST", "/incidents", json=incident.model_dump(mode="json"))
    if result:
        st.success(f"Submitted {result['incident_id']}. Watch it on the Dashboard.")


def outcome(run: dict) -> str:
    if run.get("error"):
        return f"error: {run['error']}"
    if run.get("rca"):
        return f"RCA: {run['rca']['root_cause_status']}"
    if run.get("layer1"):
        return "answered by Layer 1" if run["layer1"]["resolved"] else "Layer 1 unresolved"
    return "-"


CSS = """
<style>
.card{border:1px solid #3a3a3a;border-radius:12px;padding:14px 16px;background:#202020;height:100%}
.card h4{margin:0 0 8px 0;font-size:.8rem;letter-spacing:.06em;text-transform:uppercase;color:#aaa}
.card p{margin:.3rem 0;line-height:1.45}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.75rem;font-weight:700;margin-left:6px}
.b-green{background:#12351f;color:#5fd38d}.b-amber{background:#3b2f0e;color:#f2c14e}
.b-blue{background:#10304a;color:#6cb6ff}.b-red{background:#41161a;color:#ff7b82}.b-grey{background:#333;color:#ccc}
.hdr{border:1px solid #3a3a3a;border-radius:12px;padding:16px 18px;background:#1b1b1b;margin-bottom:10px}
.hdr h2{margin:0;font-size:1.4rem}.meta{color:#aaa;font-size:.85rem;margin-top:4px}
.pipe{display:flex;gap:8px;margin:10px 0 14px 0;flex-wrap:wrap}
.step{padding:6px 12px;border-radius:8px;font-size:.8rem;background:#262626;color:#888;border:1px solid #333}
.step.done{color:#5fd38d;border-color:#1f5f3a}.step.now{color:#6cb6ff;border-color:#235c8c}.step.skip{color:#777;text-decoration:line-through}
.fact{color:#ddd;font-size:.9rem;margin:2px 0}.fact b{color:#aaa;font-weight:600}
</style>
"""


def _esc(value) -> str:
    return html.escape(str(value if value is not None else "-"))


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge b-{kind}">{_esc(text)}</span>'


def _run_badge(run: dict) -> str:
    if run["status"] == "failed":
        return _badge("FAILED", "red")
    if run["status"] == "awaiting_approval":
        return _badge("AWAITING APPROVAL", "amber")
    if run["status"] != "done":
        return _badge("RUNNING", "blue")
    if run.get("resolution_status") == "escalated":
        return _badge("ESCALATED", "amber")
    return _badge("RESOLVED", "green")


def _duration(events: list[dict]) -> str:
    try:
        t0, t1 = (time.strptime(events[i]["time"], "%H:%M:%S") for i in (0, -1))
        seconds = (t1.tm_hour * 3600 + t1.tm_min * 60 + t1.tm_sec) - (t0.tm_hour * 3600 + t0.tm_min * 60 + t0.tm_sec)
        return f"{max(seconds, 0)}s"
    except (IndexError, ValueError, KeyError):
        return "-"


def _pipeline(run: dict) -> str:
    stages = {e["stage"] for e in run["events"]}
    done = run["status"] in ("done", "failed")
    layer1_ran = "layer1" in stages
    evidence_done = any("Evidence round" in e["message"] for e in run["events"])
    steps = [
        ("Triage", "done" if "triage" in stages else "now"),
        ("Layer 1", ("done" if layer1_ran else "skip") if "triage" in stages else ""),
        ("Evidence", "done" if evidence_done else ("now" if "layer2" in stages else "")),
        ("Resolution", "done" if done else ("now" if evidence_done else "")),
    ]
    marks = {"done": "✔ ", "now": "● ", "skip": "", "": ""}
    return '<div class="pipe">' + "".join(
        f'<div class="step {state}">{marks[state]}{name}{" (skipped)" if state == "skip" else ""}</div>' for name, state in steps
    ) + "</div>"


@st.cache_data(show_spinner=False)
def _screenshot(incident_id: str, observed_at: str):
    """Fetch the evidence image once per observation, not on every 3-second refresh."""
    try:
        resp = requests.get(f"{API}/incidents/{incident_id}/screenshot", timeout=10)
        return resp.content if resp.ok else None
    except requests.RequestException:
        return None


def _agent_card(title: str, badge_html: str, facts: list[tuple[str, object]], seconds=None) -> str:
    rows = "".join(f'<div class="fact"><b>{_esc(k)}:</b> {_esc(v)}</div>' for k, v in facts if v not in (None, "", []))
    timing = f'<div class="meta">{seconds}s</div>' if seconds is not None else ""
    return f'<div class="card"><h4>{title}{badge_html}</h4>{rows}{timing}</div>'


def _browser_card(incident_id: str, finding: dict):
    report = finding.get("report") or {}
    if finding.get("status") != "completed":
        st.markdown(_agent_card("🌐 Browser", _badge("FAILED", "red"), [("error", finding.get("error"))]), unsafe_allow_html=True)
        return
    row = (report.get("rows") or [{}])[0]
    kind = {"MATCH": "green", "MULTIPLE": "amber"}.get(report.get("result"), "red")
    if report.get("screenshot"):
        image = _screenshot(incident_id, report.get("observed_at", ""))
        if image:
            st.image(image, caption="Dashboard as seen by the browser agent", width="stretch")
        else:
            st.caption("Screenshot could not be loaded.")
    else:
        st.caption("No screenshot was captured for this check.")
    st.markdown(_agent_card("🌐 Browser", _badge(report.get("result", "?"), kind), [
        ("status", row.get("status")), ("retries", row.get("retry_count")), ("RDR ID", row.get("rdr_id") or "not created"),
        ("last error", row.get("last_error")), ("observed", report.get("observed_at")),
    ], finding.get("seconds")), unsafe_allow_html=True)


def _db_card(finding: dict):
    report = finding.get("report") or {}
    if finding.get("status") != "completed":
        st.markdown(_agent_card("🗄 Database", _badge("FAILED", "red"), [("error", finding.get("error"))]), unsafe_allow_html=True)
        return
    kind = {"VERIFIED": "green", "PROPOSE_WRITE": "amber"}.get(report.get("classification"), "grey")
    facts = [("record found", report.get("record_found"))] + [("", f) for f in report.get("findings", [])[:6]]
    facts.append(("write", report.get("write_result") or "none proposed"))
    st.markdown(_agent_card("🗄 Database", _badge(report.get("classification", "?"), kind), facts, finding.get("seconds")), unsafe_allow_html=True)


def _logs_card(finding: dict):
    report = finding.get("report") or {}
    if finding.get("status") != "completed":
        st.markdown(_agent_card("📜 Logs", _badge("FAILED", "red"), [("error", finding.get("error"))]), unsafe_allow_html=True)
        return
    kind = {"confirmed": "green", "probable": "amber"}.get(report.get("root_cause_status"), "grey")
    facts = [("classification", report.get("classification")), ("cause", report.get("root_cause")), ("confidence", report.get("confidence"))]
    facts += [("evidence", e) for e in report.get("evidence", [])[:3]]
    st.markdown(_agent_card("📜 Logs", _badge(str(report.get("root_cause_status", "?")), kind), facts, finding.get("seconds")), unsafe_allow_html=True)
    if report.get("timeline"):
        with st.expander("Log timeline"):
            for entry in report["timeline"]:
                st.text(entry)


def _docs_card(finding: dict):
    report = finding.get("report") or {}
    facts = [("result", report.get("result"))] + [("", f"{x.get('label')}: {x.get('citation')}") for x in report.get("findings", [])[:3]]
    st.markdown(_agent_card("📚 Docs", _badge(str(report.get("result", "?")), "grey"), facts, finding.get("seconds")), unsafe_allow_html=True)


def detail(run: dict):
    st.markdown(CSS, unsafe_allow_html=True)
    inc = run["incident"]
    ident = " · ".join(f"{label} {inc[key]}" for label, key in (("VIN", "vin"), ("Dealer", "dealer_id"), ("Reported", "reported_status")) if inc.get(key))
    st.markdown(
        f'<div class="hdr"><h2>{_esc(inc["incident_id"])}: {_esc(inc["title"])} {_run_badge(run)}</h2>'
        f'<div class="meta">{_esc(run["route"] or "-")} · {run["tokens"]:,} tokens · ${run.get("cost_usd", 0):.4f} · {_duration(run["events"])} · {_esc(ident)}</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(_pipeline(run), unsafe_allow_html=True)

    if run["status"] == "awaiting_approval":
        st.warning("A database change needs your approval.")
        st.json(run["pending"])
        col1, col2 = st.columns(2)
        if col1.button("Approve", type="primary"):
            call("POST", f"/incidents/{inc['incident_id']}/decision", json={"approve": True})
            st.rerun()
        if col2.button("Reject"):
            call("POST", f"/incidents/{inc['incident_id']}/decision", json={"approve": False})
            st.rerun()

    if run.get("error"):
        st.error(run["error"])

    if run.get("layer1"):
        l1 = run["layer1"]
        sources = "".join(f'<div class="fact">{_esc(src)}</div>' for src in l1["sources"])
        st.markdown(
            f'<div class="card"><h4>Layer 1 answer{_badge("ANSWERED" if l1["resolved"] else "ESCALATED", "green" if l1["resolved"] else "amber")}</h4>'
            f'<p>{_esc(l1["answer"])}</p>{sources}</div>', unsafe_allow_html=True)

    if run.get("rca"):
        rca = run["rca"]
        kind = {"confirmed": "green", "probable": "amber"}.get(rca["root_cause_status"], "grey")
        left, right = st.columns(2)
        left.markdown(f'<div class="card"><h4>Root cause{_badge(rca["root_cause_status"].upper(), kind)}</h4><p>{_esc(rca["root_cause"])}</p></div>', unsafe_allow_html=True)
        checks = "".join(f'<div class="fact">• {_esc(c)}</div>' for c in rca["pending_checks"]) or '<div class="fact">Pending checks: none</div>'
        right.markdown(f'<div class="card"><h4>Recommended action</h4><p>{_esc(rca["recommended_action"])}</p>{checks}</div>', unsafe_allow_html=True)

    findings = run.get("findings") or {}
    if findings:
        st.markdown("**Evidence from agents**")
        order = [name for name in ("browser-agent", "db-agent", "logs-agent", "docs-agent") if name in findings]
        for col, name in zip(st.columns(len(order)), order):
            with col:
                finding = findings[name]
                {"browser-agent": lambda f: _browser_card(inc["incident_id"], f), "db-agent": _db_card,
                 "logs-agent": _logs_card, "docs-agent": _docs_card}[name](finding)
                with st.expander("Raw output"):
                    st.json(finding)

    with st.expander("Activity trace"):
        for event in run["events"][-60:]:
            st.text(f"{event['time']}  {event['message']}")


@st.fragment(run_every=3)
def live_dashboard():
    runs = call("GET", "/incidents")
    if runs is None:
        return
    if not runs:
        st.info("No incidents yet. Submit one from the Submit page.")
        return

    st.dataframe(
        [
            {"incident": r["incident"]["incident_id"], "title": r["incident"]["title"],
             "status": r["status"], "current step": r["current_step"] or "-",
             "route": r["route"] or "-", "outcome": outcome(r)}
            for r in runs
        ],
        hide_index=True,
        width="stretch",
    )

    ids = [r["incident"]["incident_id"] for r in runs]
    chosen = st.selectbox("Open incident", ids, index=len(ids) - 1)
    run = call("GET", f"/incidents/{chosen}")
    if run:
        detail(run)


def dashboard_page():
    st.header("Dashboard")
    st.caption("Updates every 3 seconds.")
    live_dashboard()


st.set_page_config(page_title="Incident Resolver", layout="wide")
page = st.sidebar.radio("Page", ["Submit incident", "Dashboard"])
if page == "Submit incident":
    submit_page()
else:
    dashboard_page()
