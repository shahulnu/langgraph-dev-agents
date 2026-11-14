"""The LangGraph workflow:

parse_diff -> plan_review --(Send, one per reviewer, in parallel)--> review -> verify -> decide

Code owns parsing, routing, verification and the verdict. Claude only runs inside `review`."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app import settings
from app.code_review import checks
from app.code_review.diff import parse_diff
from app.code_review.models import ReviewState, ReviewTask
from app.code_review.reviewers import review_live, review_replay


def _step(node: str, t0: float, detail: str) -> dict:
    return {"node": node, "ms": round((time.perf_counter() - t0) * 1000), "detail": detail}


def parse_node(state: ReviewState) -> dict:
    t0 = time.perf_counter()
    files = parse_diff(state["case"]["diff"])
    added = sum(l["kind"] == "add" for f in files for l in f["lines"])
    return {"files": files, "trace": [_step("parse_diff", t0, f"{len(files)} file(s), {added} added line(s)")]}


def plan_node(state: ReviewState) -> dict:
    t0 = time.perf_counter()
    plan = checks.plan_reviewers(state["files"])
    names = ", ".join(p["reviewer"] for p in plan) or "none"
    return {"plan": plan, "trace": [_step("plan_review", t0, f"Reviewers: {names}")]}


def fan_out(state: ReviewState):
    """Conditional edge: one parallel branch per planned reviewer, or straight to verify."""
    if not state["plan"]:
        return "verify"
    return [
        Send("review", {"case": state["case"], "mode": state["mode"], "files": state["files"], "reviewer": p["reviewer"]})
        for p in state["plan"]
    ]


def review_node(task: ReviewTask) -> dict:
    t0 = time.perf_counter()
    if task["mode"] == "live":
        result = review_live(task["case"], task["files"], task["reviewer"])
        u = result["usage"]
        detail = f"{result['model']}, {u['inputTokens']} in / {u['outputTokens']} out tokens"
    else:
        result = review_replay(task["case"], task["reviewer"])
        detail = "Loaded recorded live output"
    n = len(result["output"]["findings"])
    return {"reviews": [result], "trace": [_step(f"review:{task['reviewer']}", t0, f"{n} finding(s). {detail}")]}


def verify_node(state: ReviewState) -> dict:
    t0 = time.perf_counter()
    reviews = state.get("reviews", [])
    findings, held = checks.verify_findings(state["files"], reviews)
    total = sum(len(r["output"]["findings"]) for r in reviews)
    corrected = sum(f["lineCorrected"] for f in findings)
    detail = f"{total - len(held)} of {total} quotes found on added lines; {len(held)} held; {corrected} line number(s) corrected"
    return {"findings": findings, "held": held, "trace": [_step("verify", t0, detail)]}


def decide_node(state: ReviewState) -> dict:
    t0 = time.perf_counter()
    verdict, reason = checks.decide(state["findings"])
    comment = checks.compose_comment(state["findings"], verdict, reason)
    return {"verdict": verdict, "verdict_reason": reason, "comment": comment, "trace": [_step("decide", t0, verdict)]}


def build_graph():
    g = StateGraph(ReviewState)
    g.add_node("parse_diff", parse_node)
    g.add_node("plan_review", plan_node)
    g.add_node("review", review_node)
    g.add_node("verify", verify_node)
    g.add_node("decide", decide_node)
    g.add_edge(START, "parse_diff")
    g.add_edge("parse_diff", "plan_review")
    g.add_conditional_edges("plan_review", fan_out, ["review", "verify"])
    g.add_edge("review", "verify")
    g.add_edge("verify", "decide")
    g.add_edge("decide", END)
    return g.compile()


GRAPH = build_graph()


def run_review(case: dict, mode: str) -> dict:
    """Run the graph for one pull request and shape the result for the API."""
    final = GRAPH.invoke({"case": case, "mode": mode, "reviews": [], "trace": []})
    reviews = sorted(final.get("reviews", []), key=lambda r: ["correctness", "security", "tests"].index(r["reviewer"]))
    tin = sum(r["usage"]["inputTokens"] for r in reviews)
    tout = sum(r["usage"]["outputTokens"] for r in reviews)
    return {
        "caseId": case["id"],
        "mode": mode,
        "engine": "python",
        "model": reviews[0]["model"] if reviews else settings.MODEL,
        "case": {k: case[k] for k in ("id", "title", "author", "language", "description", "teaser")},
        "files": final["files"],
        "plan": final["plan"],
        "reviews": [
            {"reviewer": r["reviewer"], "notes": r["output"]["notes"], "findingCount": len(r["output"]["findings"]), "usage": r["usage"], "ms": r["ms"]}
            for r in reviews
        ],
        "findings": final["findings"],
        "held": final["held"],
        "verdict": final["verdict"],
        "verdictReason": final["verdict_reason"],
        "comment": final["comment"],
        "trace": final["trace"],
        "usage": {
            "inputTokens": tin,
            "outputTokens": tout,
            "costUsd": round(tin * settings.PRICE_IN_PER_MTOK / 1e6 + tout * settings.PRICE_OUT_PER_MTOK / 1e6, 4),
        } if mode == "live" else None,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "raw": {r["reviewer"]: r["output"] for r in reviews},
    }
