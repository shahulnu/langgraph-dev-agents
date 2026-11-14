"""Schemas for the code review graph: what each reviewer returns and the graph's shared state."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Reviewer = Literal["correctness", "security", "tests"]
Severity = Literal["high", "medium", "low"]


class ReviewerFinding(BaseModel):
    file: str = Field(description="Path of the file, exactly as in the diff.")
    line: int = Field(description="New line number of the added line the finding is about.")
    quote: str = Field(description="That added line's code, copied character for character, without the '+' and line number.")
    severity: Severity
    title: str = Field(description="A short title, under 10 words.")
    explanation: str = Field(description="One or two plain sentences: what is wrong and what it causes.")
    suggestion: str = Field(description="One sentence on how to fix it.")


class ReviewerOutput(BaseModel):
    findings: list[ReviewerFinding]
    notes: str = Field(description="One sentence on what you checked.")


class ReviewState(TypedDict, total=False):
    """Shared state. Fields with a reducer collect results from the reviewers that run in parallel."""

    case: dict
    mode: Literal["live", "replay"]
    files: list[dict]
    plan: list[dict]
    reviews: Annotated[list[dict], operator.add]
    findings: list[dict]
    held: list[dict]
    verdict: str
    verdict_reason: str
    comment: str
    trace: Annotated[list[dict], operator.add]


class ReviewTask(TypedDict):
    """Input for one reviewer branch, sent with LangGraph's Send API."""

    case: dict
    mode: Literal["live", "replay"]
    files: list[dict]
    reviewer: Reviewer
