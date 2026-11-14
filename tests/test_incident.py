"""Tests for the Incident Debugger's code layer. No model calls: graph tests replay recorded runs,
which still execute every tool call and the reproduction tests for real."""

import pytest

from app.incident import graph, runner, world

GIFT_TEST = """from checkout.cart import make_cart
from checkout.orders import place_order


def test_gift_order_is_placed():
    cart = make_cart([{"price": 10.0, "qty": 1}], {"city": "X"}, True, "hi")
    assert place_order("O1", cart, [])["gift_message"] == "hi"
"""


def test_world_tools_return_ids():
    inc = world.incident("INC-201")
    assert "KeyError: 'shipping_address'" in world.error_summary(inc)
    assert world.search_logs(inc, "KeyError", level="ERROR").startswith("7 matching")
    assert "commit a41c9e2" in world.show_diff("a41c9e2")
    assert "18" in world.grep_code(r"shipping_address", "v1.4.2")


def test_read_file_stays_inside_the_service():
    assert world.read_file("../../history.json").startswith("No file")
    assert world.read_file("checkout/orders.py", "v9").startswith("No file")


@pytest.mark.parametrize("ref, ok", [
    ("log:L0001", True), ("log:L9999", False), ("commit:a41c9e2", True), ("commit:deadbee", False),
    ("code:checkout/orders.py:18@v1.4.2", True), ("code:checkout/orders.py:99", False),
    ("metric:error_rate_pct", True), ("note:N1", True), ("note:N9", False), ("deploy:v1.4.2", True), ("nonsense", False),
])
def test_evidence_references_are_checked(ref, ok):
    assert world.verify_ref(world.incident("INC-201"), ref) is ok


@pytest.mark.parametrize("code, reason", [
    ("import os\ndef test_a(): pass", "Imports os"),
    ("import subprocess\ndef test_a(): pass", "Imports subprocess"),
    ("def test_a(): open('x')", "open()"),
    ("def test_a(): eval('1')", "eval()"),
    ("def test_a(): ().__class__", "dunder"),
    ("from checkout import cart", "no test_ function"),
])
def test_unsafe_tests_are_refused(code, reason):
    assert any(reason.lower() in p.lower() for p in runner.check_test(code))


def test_proof_needs_fail_on_deployed_and_pass_on_previous():
    r = runner.run_on_both(GIFT_TEST)
    assert [x["outcome"] for x in r["runs"]] == ["passed", "failed"] and r["proves"]


def test_a_test_that_passes_on_both_versions_proves_nothing():
    r = runner.run_on_both("from checkout.payments import GATEWAY_TIMEOUT_SECONDS\n\ndef test_t():\n    assert GATEWAY_TIMEOUT_SECONDS == 8\n")
    assert [x["outcome"] for x in r["runs"]] == ["passed", "passed"] and not r["proves"]


def _state(report, proves=False):
    findings = [{"repro": {"proves": proves}}]
    return {"incident": {"id": "INC-201"}, "report": report, "findings": findings}


def test_verdict_rules():
    rep = {"caused_by_deploy": True, "suspected_commit": "a41c9e2", "suspected_location": "checkout/orders.py:18", "evidence": ["log:L0050", "commit:a41c9e2"]}
    assert graph.verdict(_state(rep, proves=True))["verdict"] == "confirmed"
    assert graph.verdict(_state(rep))["verdict"] == "likely"
    assert graph.verdict(_state({**rep, "evidence": ["log:L9999"]}))["verdict"] == "inconclusive"
    assert graph.verdict(_state({**rep, "caused_by_deploy": False}))["verdict"] == "not_caused_by_deploy"
    assert graph.verdict(_state({**rep, "caused_by_deploy": False}, proves=True))["verdict"] == "inconclusive"
    assert graph.verdict(_state({**rep, "suspected_commit": "zzzzzzz"}, proves=True))["verdict"] != "confirmed"


async def test_budget_forces_a_conclusion():
    cmd = await graph.orchestrator({"incident": {"id": "INC-201"}, "mode": "replay", "steps": [], "delegations": graph.MAX_DELEGATIONS})
    assert cmd.goto == "report"


@pytest.mark.parametrize("inc, verdict", [("INC-201", "confirmed"), ("INC-202", "confirmed"), ("INC-203", "not_caused_by_deploy")])
async def test_replayed_investigations_reach_the_recorded_verdicts(inc, verdict):
    r = await graph.investigate(inc, "replay")
    assert r["verdict"] == verdict
    assert all(e["verified"] for f in r["findings"] for e in f["evidence"])
