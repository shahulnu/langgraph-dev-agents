"""The model side of the Incident Debugger: the orchestrator's decisions, the specialists (each a
small LangGraph subgraph with its own tool loop) and the final report. Replay mode uses a recorded
live run but re-executes every tool call and the reproduction test for real."""

from __future__ import annotations

import asyncio
import json
import operator
import time
from functools import lru_cache
from typing import Annotated, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app import settings
from app.incident import prompts, runner, world
from app.incident.models import Decision, Finding, Report, ReproTest

RECORDED_PATH = settings.DATA_DIR / "incident" / "recorded.json"
MAX_SPECIALIST_TURNS = 6


class ModelDeclinedError(Exception):
    pass


class ReplayMissingError(Exception):
    pass


@lru_cache(maxsize=1)
def chat() -> ChatAnthropic:
    return ChatAnthropic(
        model=settings.MODEL,
        max_tokens=16000,
        effort="medium",
        # On a safety-classifier decline, the API retries on Anthropic's recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        model_kwargs={"fallbacks": "default"},
    )


def cached(text: str) -> SystemMessage:
    return SystemMessage(content=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}])


def usage_of(msg) -> dict:
    u = getattr(msg, "usage_metadata", None) or {}
    return {"inputTokens": u.get("input_tokens", 0), "outputTokens": u.get("output_tokens", 0)}


def _check_refusal(msg) -> None:
    if msg.response_metadata.get("stop_reason") == "refusal":
        raise ModelDeclinedError("The model declined this step of the investigation.")


# ---------- tools ----------

def tools_for(specialist: str, inc: dict) -> list[StructuredTool]:
    def search_logs(query: str = "", level: str | None = None, since: str | None = None, until: str | None = None, limit: int = 25) -> str:
        """Search log lines by text (case-insensitive), level (ERROR/WARN/INFO) and ISO time range. Returns IDs like L0042."""
        return world.search_logs(inc, query, level, since, until, limit)

    def error_summary() -> str:
        """Count ERROR and WARN messages before and after the deploy, with the first occurrence of each."""
        return world.error_summary(inc)

    def get_notes() -> str:
        """Support tickets, vendor status updates and on-call chat notes, with IDs like N1."""
        return world.get_notes(inc)

    def get_metrics(name: str) -> str:
        """10-minute averages for a metric. An unknown name returns the list of available metrics."""
        return world.get_metrics(inc, name)

    def get_deploys() -> str:
        """Deploy history with times and the commits in each deploy."""
        return world.get_deploys()

    def list_commits() -> str:
        """Commits in recent deploys: short SHA, time, author, title, files."""
        return world.list_commits()

    def show_diff(sha: str) -> str:
        """Unified diff of a commit."""
        return world.show_diff(sha)

    def read_file(path: str, version: str = "v1.4.2", start: int = 1, end: int = 200) -> str:
        """Read a source file with line numbers. version is v1.4.2 (deployed) or v1.4.1 (before the deploy)."""
        return world.read_file(path, version, start, end)

    def grep_code(pattern: str, version: str = "v1.4.2") -> str:
        """Search the source of a version with a regular expression. Returns path:line: text."""
        return world.grep_code(pattern, version)

    fns = {
        "log_analyst": [search_logs, error_summary, get_notes],
        "metrics_analyst": [get_metrics, get_deploys, get_notes],
        "change_investigator": [list_commits, show_diff, get_deploys],
        "code_reader": [read_file, grep_code],
        "reproducer": [read_file, grep_code],
    }[specialist]
    tools = [StructuredTool.from_function(f) for f in fns]
    if specialist == "reproducer":
        tools.append(StructuredTool.from_function(lambda **kw: "Submitted.", name="submit_test", description="Submit the reproduction test. Call once, last.", args_schema=ReproTest))
    else:
        tools.append(StructuredTool.from_function(lambda **kw: "Submitted.", name="submit_finding", description="Submit your finding. Call once, last.", args_schema=Finding))
    return tools


# ---------- a specialist as a subgraph ----------

class SpecialistState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    calls: Annotated[list[dict], operator.add]
    submitted: dict | None
    turns: int
    usage: Annotated[list[dict], operator.add]


def build_specialist(tools: list[StructuredTool]):
    by_name = {t.name: t for t in tools}
    llm = chat().bind_tools(tools)

    async def agent(state: SpecialistState) -> dict:
        ai = await llm.ainvoke(state["messages"])
        _check_refusal(ai)
        return {"messages": [ai], "turns": state["turns"] + 1, "usage": [usage_of(ai)]}

    def run_tools(state: SpecialistState) -> dict:
        ai: AIMessage = state["messages"][-1]
        out, calls, submitted = [], [], None
        for tc in ai.tool_calls:
            if tc["name"] in ("submit_finding", "submit_test"):
                submitted = tc["args"]
                content = "Submitted."
            elif tc["name"] in by_name:
                content = by_name[tc["name"]].invoke(tc["args"])
                calls.append({"tool": tc["name"], "args": tc["args"], "result": content[:1500]})
            else:
                content = f"Unknown tool {tc['name']}."
            out.append(ToolMessage(content=content, tool_call_id=tc["id"]))
        return {"messages": out, "calls": calls, "submitted": submitted}

    def route(state: SpecialistState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and state["turns"] <= MAX_SPECIALIST_TURNS:
            return "tools"
        return END

    def after_tools(state: SpecialistState) -> str:
        return END if state["submitted"] is not None else "agent"

    g = StateGraph(SpecialistState)
    g.add_node("agent", agent)
    g.add_node("tools", run_tools)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, ["tools", END])
    g.add_conditional_edges("tools", after_tools, ["agent", END])
    return g.compile()


async def specialist_live(name: str, inc: dict, task: str, case_file: str) -> dict:
    tools = tools_for(name, inc)
    sub = build_specialist(tools)
    system = cached(prompts.SPECIALIST_RULES + "\n\n" + prompts.ROLES[name])
    final = await sub.ainvoke({
        "messages": [system, HumanMessage(content=prompts.specialist_message(inc, task, case_file))],
        "calls": [], "submitted": None, "turns": 0, "usage": [],
    })
    usage = {k: sum(u[k] for u in final["usage"]) for k in ("inputTokens", "outputTokens")}
    return {"calls": final["calls"], "submitted": final["submitted"], "usage": usage}


def specialist_replay(name: str, inc: dict, recorded: dict) -> dict:
    by_name = {t.name: t for t in tools_for(name, inc)}
    calls = [{"tool": c["tool"], "args": c["args"], "result": by_name[c["tool"]].invoke(c["args"])[:1500]} for c in recorded["calls"]]
    return {"calls": calls, "submitted": recorded["submitted"], "usage": {"inputTokens": 0, "outputTokens": 0}}


async def run_repro(test_code: str) -> dict:
    return await asyncio.to_thread(runner.run_on_both, test_code)


# ---------- orchestrator and report ----------

async def decide_live(inc: dict, case_file: str, budget_left: int) -> tuple[dict, dict]:
    model = chat().with_structured_output(Decision, method="json_schema", include_raw=True)
    r = await model.ainvoke([
        cached(prompts.ORCHESTRATOR),
        HumanMessage(content=prompts.specialist_message(inc, f"Decide the next step. Delegations left: {budget_left}.", case_file)),
    ])
    _check_refusal(r["raw"])
    if r["parsed"] is None:
        raise RuntimeError(f"Orchestrator output didn't match the schema: {r.get('parsing_error')}")
    return r["parsed"].model_dump(), usage_of(r["raw"])


async def report_live(inc: dict, case_file: str) -> tuple[dict, dict]:
    model = chat().with_structured_output(Report, method="json_schema", include_raw=True)
    r = await model.ainvoke([cached(prompts.REPORT), HumanMessage(content=prompts.specialist_message(inc, "Write the final report.", case_file))])
    _check_refusal(r["raw"])
    if r["parsed"] is None:
        raise RuntimeError(f"Report didn't match the schema: {r.get('parsing_error')}")
    return r["parsed"].model_dump(), usage_of(r["raw"])


def recorded() -> dict:
    if not RECORDED_PATH.exists():
        return {"cases": {}}
    return json.loads(RECORDED_PATH.read_text(encoding="utf-8"))


def replay_case(incident_id: str) -> dict:
    case = recorded().get("cases", {}).get(incident_id)
    if case is None:
        raise ReplayMissingError(f"No recorded run for {incident_id}. Run the eval with --live --record.")
    return case


def now_ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)
