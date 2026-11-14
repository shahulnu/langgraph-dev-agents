"""The Incident Debugger graph: an orchestrator that delegates to specialists until it can conclude.

orchestrator --Command(goto)--> delegate (runs one specialist subgraph) --> orchestrator ...
orchestrator --conclude or budget--> report --> verdict

Code owns the budget, the evidence check, the reproduction run and the verdict."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app import settings
from app.incident import agents, world
from app.incident.models import CaseState

MAX_DELEGATIONS = 8


def _verify(inc: dict, evidence: list[dict]) -> list[dict]:
    return [{**e, "verified": world.verify_ref(inc, e.get("ref", ""))} for e in evidence]


def case_file(state: CaseState) -> str:
    """What the orchestrator and specialists see: findings so far, with unverified evidence removed."""
    deploy = world.history()["deploys"][-1]
    out = [f"Deploy {deploy['version']} at {deploy['at']} with commits {', '.join(deploy['commits'])}. Previous version v1.4.1."]
    for i, f in enumerate(state.get("findings", []), 1):
        out.append(f"\n#{i} {f['specialist']}: {f['task']}")
        if f.get("summary"):
            out.append(f["summary"])
        for e in f.get("evidence", []):
            if e["verified"]:
                out.append(f"  - {e['ref']}: {e['detail']}")
        dropped = sum(not e["verified"] for e in f.get("evidence", []))
        if dropped:
            out.append(f"  ({dropped} cited reference(s) didn't exist and were dropped)")
        if f.get("repro"):
            r = f["repro"]
            if not r["ran"]:
                out.append(f"  Test not run: {'; '.join(r['problems'])}")
            else:
                out.append("  Test results (test:repro): " + ", ".join(f"{x['version']} {x['outcome']}" for x in r["runs"])
                           + (". Proves the deploy introduced the bug." if r["proves"] else ". Does not prove a regression."))
                failing = next((x for x in r["runs"] if x["outcome"] != "passed"), None)
                if failing:
                    out.append("  Output: " + failing["output"].splitlines()[-1][:200])
    if not state.get("findings"):
        out.append("\nNo findings yet.")
    return "\n".join(out)


async def orchestrator(state: CaseState) -> Command[Literal["delegate", "report"]]:
    t0 = time.perf_counter()
    used = state.get("delegations", 0)
    if used >= MAX_DELEGATIONS:
        step = {"n": len(state.get("steps", [])) + 1, "reasoning": "Delegation budget used; concluding with the evidence so far.", "action": "conclude", "specialist": None, "task": "", "ms": 0, "by": "code"}
        return Command(goto="report", update={"steps": [step]})
    inc = state["incident"]
    if state["mode"] == "live":
        decision, usage = await agents.decide_live(inc, case_file(state), MAX_DELEGATIONS - used)
    else:
        rec = agents.replay_case(inc["id"])["decisions"]
        decision, usage = rec[len(state.get("steps", []))], {"inputTokens": 0, "outputTokens": 0}
    step = {"n": len(state.get("steps", [])) + 1, **decision, "ms": agents.now_ms(t0), "by": "orchestrator"}
    if decision["action"] == "conclude" or not decision.get("specialist"):
        return Command(goto="report", update={"steps": [step], "usage": [usage]})
    return Command(goto="delegate", update={"steps": [step], "pending": decision, "usage": [usage]})


async def delegate(state: CaseState) -> dict:
    t0 = time.perf_counter()
    inc, task = state["incident"], state["pending"]
    name = task["specialist"]
    full = world.incident(inc["id"])
    if state["mode"] == "live":
        result = await agents.specialist_live(name, full, task["task"], case_file(state))
    else:
        rec = agents.replay_case(inc["id"])["specialists"][len(state.get("findings", []))]
        result = agents.specialist_replay(name, full, rec)
    sub = result["submitted"] or {}
    finding = {"specialist": name, "task": task["task"], "calls": result["calls"], "submitted": sub}
    if name == "reproducer":
        code = sub.get("test_code", "")
        finding["test_code"] = code
        finding["summary"] = sub.get("what_it_checks", "No test was submitted.")
        finding["repro"] = await agents.run_repro(code) if code else {"ran": False, "problems": ["No test was submitted."], "runs": [], "proves": False}
        finding["evidence"] = [{"ref": "test:repro", "detail": "Reproduction test run on v1.4.1 and v1.4.2", "verified": finding["repro"]["ran"]}]
    else:
        finding["summary"] = sub.get("summary", "The specialist didn't submit a finding.")
        finding["confidence"] = sub.get("confidence")
        finding["evidence"] = _verify(full, sub.get("evidence", []))
    finding["ms"] = agents.now_ms(t0)
    finding["usage"] = result["usage"]
    return {"findings": [finding], "delegations": state.get("delegations", 0) + 1, "usage": [result["usage"]]}


async def report(state: CaseState) -> dict:
    inc = state["incident"]
    if state["mode"] == "live":
        rep, usage = await agents.report_live(inc, case_file(state))
    else:
        rep, usage = agents.replay_case(inc["id"])["report"], {"inputTokens": 0, "outputTokens": 0}
    return {"report": rep, "usage": [usage]}


def verdict(state: CaseState) -> dict:
    """The verdict comes from code: proof, evidence and the report's claims, checked against the world."""
    inc, rep = world.incident(state["incident"]["id"]), state["report"]
    commits = {c["sha"] for c in world.history()["commits"]}
    proven = any(f.get("repro", {}).get("proves") for f in state.get("findings", []))
    verified = [r for r in rep.get("evidence", []) if world.verify_ref(inc, r)]
    commit_ok = (rep.get("suspected_commit") or "")[:7] in commits
    loc = rep.get("suspected_location")
    loc_ok = bool(loc) and world.verify_ref(inc, f"code:{loc}")
    if not rep.get("caused_by_deploy"):
        if proven:
            v, why = "inconclusive", "The report says the deploy isn't the cause, but a test proved a regression in it."
        else:
            v, why = "not_caused_by_deploy", "The evidence points away from the deploy, and no test showed a regression."
    elif proven and commit_ok:
        v, why = "confirmed", "A test fails on v1.4.2 and passes on v1.4.1, and the report names a commit from the deploy."
    elif commit_ok and len(verified) >= 2:
        v, why = "likely", "The evidence agrees on a commit in the deploy, but no test proved it."
    else:
        v, why = "inconclusive", "Not enough verified evidence or proof."
    if loc and not loc_ok:
        why += f" The cited location {loc} doesn't exist and was ignored."
    return {"verdict": v, "verdict_reason": why}


def build_graph():
    g = StateGraph(CaseState)
    g.add_node("orchestrator", orchestrator)
    g.add_node("delegate", delegate)
    g.add_node("report", report)
    g.add_node("verdict", verdict)
    g.add_edge(START, "orchestrator")
    g.add_edge("delegate", "orchestrator")
    g.add_edge("report", "verdict")
    g.add_edge("verdict", END)
    return g.compile()


GRAPH = build_graph()


async def investigate(incident_id: str, mode: str) -> dict:
    inc = world.incident(incident_id)
    meta = {k: inc[k] for k in ("id", "title", "teaser", "opened", "description")}
    t0 = time.perf_counter()
    final = await GRAPH.ainvoke({"incident": meta, "mode": mode, "steps": [], "findings": [], "delegations": 0, "usage": []}, {"recursion_limit": 60})
    tin = sum(u["inputTokens"] for u in final["usage"])
    tout = sum(u["outputTokens"] for u in final["usage"])
    rep = final["report"]
    return {
        "incidentId": incident_id,
        "mode": mode,
        "engine": "python",
        "model": settings.MODEL,
        "incident": meta,
        "steps": final["steps"],
        "findings": final["findings"],
        "report": {**rep, "evidence": [{"ref": r, "verified": world.verify_ref(inc, r)} for r in rep.get("evidence", [])]},
        "verdict": final["verdict"],
        "verdictReason": final["verdict_reason"],
        "delegations": final["delegations"],
        "budget": MAX_DELEGATIONS,
        "ms": agents.now_ms(t0),
        "usage": {"inputTokens": tin, "outputTokens": tout,
                  "costUsd": round(tin * settings.PRICE_IN_PER_MTOK / 1e6 + tout * settings.PRICE_OUT_PER_MTOK / 1e6, 4)} if mode == "live" else None,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


def to_recording(result: dict) -> dict:
    """What replay needs: the orchestrator's decisions, each specialist's tool calls and submission, the report."""
    return {
        "decisions": [{k: s[k] for k in ("reasoning", "action", "specialist", "task")} for s in result["steps"] if s.get("by") == "orchestrator"],
        "specialists": [{"name": f["specialist"], "calls": [{"tool": c["tool"], "args": c["args"]} for c in f["calls"]], "submitted": f["submitted"]} for f in result["findings"]],
        "report": {**result["report"], "evidence": [e["ref"] for e in result["report"]["evidence"]]},
    }
