"""Schemas for the Incident Debugger: the orchestrator's decisions, specialists' findings, the
final report, and the graph state."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Specialist = Literal["log_analyst", "change_investigator", "code_reader", "metrics_analyst", "reproducer"]


class Decision(BaseModel):
    reasoning: str = Field(description="One or two sentences: what you know so far and why this is the next step.")
    action: Literal["delegate", "conclude"]
    specialist: Specialist | None = Field(description="Who to delegate to; null when concluding.")
    task: str = Field(description="A specific question for the specialist; empty when concluding.")


class Evidence(BaseModel):
    ref: str = Field(description='Reference to something you saw: "log:L0042", "commit:a41c9e2", "code:checkout/orders.py:17@v1.4.2", "metric:<name>", "note:N1", "deploy:v1.4.2".')
    detail: str = Field(description="What it shows, in one short sentence.")


class Finding(BaseModel):
    summary: str = Field(description="Two or three sentences answering the task.")
    evidence: list[Evidence]
    confidence: Literal["low", "medium", "high"]


class ReproTest(BaseModel):
    test_code: str = Field(description="A complete pytest file. Import only from checkout, pytest, math or decimal.")
    what_it_checks: str = Field(description="One sentence: the behavior the test asserts.")


class Report(BaseModel):
    root_cause: str = Field(description="Two or three plain sentences for the on-call engineer.")
    caused_by_deploy: bool = Field(description="True if a change in the v1.4.2 deploy caused this incident's symptom.")
    suspected_commit: str | None = Field(description="Short SHA of the commit at fault, or null.")
    suspected_location: str | None = Field(description='"path:line" of the faulty code in v1.4.2, or null.')
    evidence: list[str] = Field(description="Evidence references that support the root cause, from the case file.")
    ruled_out: list[str] = Field(description="Explanations you considered and ruled out, each one sentence.")
    suggested_fix: str = Field(description="One or two sentences. An engineer will make the change.")
    confidence: Literal["low", "medium", "high"]


class CaseState(TypedDict, total=False):
    incident: dict
    mode: Literal["live", "replay"]
    steps: Annotated[list[dict], operator.add]
    findings: Annotated[list[dict], operator.add]
    pending: dict
    delegations: int
    report: dict
    verdict: str
    verdict_reason: str
    usage: Annotated[list[dict], operator.add]
