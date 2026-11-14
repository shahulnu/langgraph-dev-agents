"""The model step: one specialist reviewer reads the diff and returns findings in a fixed schema.
Live mode calls Claude through LangChain; replay mode loads a recorded live run."""

from __future__ import annotations

import json
import time
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app import settings
from app.code_review.diff import render_for_review
from app.code_review.models import ReviewerOutput
from app.code_review.prompts import system_prompt, user_message

RECORDED_PATH = settings.DATA_DIR / "code-review" / "recorded.json"


class ModelDeclinedError(Exception):
    """The model declined the request (stop_reason "refusal"), even after the server-side fallback."""


class ReplayMissingError(Exception):
    """There is no recorded run for this case and reviewer."""


@lru_cache(maxsize=1)
def _structured_model():
    llm = ChatAnthropic(
        model=settings.MODEL,
        max_tokens=16000,
        effort="medium",
        # On a safety-classifier decline, the API retries on Anthropic's recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        model_kwargs={"fallbacks": "default"},
    )
    # json_schema uses Claude's structured outputs. The default method forces a tool call,
    # which Claude Opus 5.5 rejects.
    return llm.with_structured_output(ReviewerOutput, method="json_schema", include_raw=True)


def review_live(case: dict, files: list[dict], reviewer: str) -> dict:
    messages = [
        # Cached: the system prompt is the same on every request for this reviewer.
        SystemMessage(content=[{"type": "text", "text": system_prompt(reviewer), "cache_control": {"type": "ephemeral"}}]),
        HumanMessage(content=user_message(case, render_for_review(files))),
    ]
    t0 = time.perf_counter()
    result = _structured_model().invoke(messages)
    ms = round((time.perf_counter() - t0) * 1000)

    raw = result["raw"]
    if raw.response_metadata.get("stop_reason") == "refusal":
        raise ModelDeclinedError(f"The {reviewer} reviewer declined this pull request. Review it manually.")
    parsed: ReviewerOutput | None = result["parsed"]
    if parsed is None:
        raise RuntimeError(f"The {reviewer} review didn't match the schema: {result.get('parsing_error')}")

    usage = raw.usage_metadata or {}
    return {
        "reviewer": reviewer,
        "output": parsed.model_dump(),
        "model": raw.response_metadata.get("model_name", settings.MODEL),
        "usage": {"inputTokens": usage.get("input_tokens", 0), "outputTokens": usage.get("output_tokens", 0)},
        "ms": ms,
    }


def _recorded() -> dict:
    if not RECORDED_PATH.exists():
        return {"cases": {}}
    return json.loads(RECORDED_PATH.read_text(encoding="utf-8"))


def review_replay(case: dict, reviewer: str) -> dict:
    rec = _recorded()
    entry = rec.get("cases", {}).get(case["id"], {}).get(reviewer)
    if entry is None:
        raise ReplayMissingError(f"No recorded {reviewer} review for {case['id']}. Run the eval with --live --record.")
    return {
        "reviewer": reviewer,
        "output": ReviewerOutput.model_validate(entry["output"]).model_dump(),
        "model": rec.get("model", settings.MODEL),
        "usage": entry.get("usage", {"inputTokens": 0, "outputTokens": 0}),
        "ms": 0,
    }
