"""Tests for the deterministic parts of the code review graph. No model calls."""

from app.code_review import checks
from app.code_review.cases import find_case
from app.code_review.diff import added_lines, parse_diff
from app.code_review.graph import GRAPH


def files_for(case_id: str):
    return parse_diff(find_case(case_id)["diff"])


def review(reviewer: str, *findings: dict) -> dict:
    return {"reviewer": reviewer, "output": {"findings": list(findings), "notes": ""}, "usage": {}, "ms": 0, "model": "m"}


def finding(file: str, line: int, quote: str, severity: str = "high") -> dict:
    return {"file": file, "line": line, "quote": quote, "severity": severity, "title": "t", "explanation": "e", "suggestion": "s"}


def test_parse_diff_numbers_added_lines():
    added = added_lines(files_for("PR-101"))["app/routes/users.py"]
    assert added[23].strip().startswith('f"SELECT id, name FROM users')
    assert 14 not in added  # context line, not added


def test_parse_diff_new_and_modified_files():
    files = files_for("PR-102")
    assert [f["status"] for f in files] == ["added", "added"]
    assert added_lines(files)["src/orders/paginate.ts"][12] == "  const start = page * size;"
    assert files_for("PR-103")[0]["status"] == "modified"


def test_plan_runs_security_only_when_needed():
    assert [p["reviewer"] for p in checks.plan_reviewers(files_for("PR-101"))] == ["correctness", "security", "tests"]
    assert [p["reviewer"] for p in checks.plan_reviewers(files_for("PR-103"))] == ["correctness", "tests"]


def test_verify_keeps_exact_quotes_and_holds_invented_ones():
    files = files_for("PR-102")
    good = finding("src/orders/paginate.ts", 12, "const start = page * size;")
    invented = finding("src/orders/paginate.ts", 12, "const start = (page - 1) * size;")
    verified, held = checks.verify_findings(files, [review("correctness", good, invented)])
    assert [f["line"] for f in verified] == [12]
    assert held[0]["reason"].startswith("The quoted code isn't")


def test_verify_corrects_a_wrong_line_number():
    files = files_for("PR-102")
    off_by_two = finding("src/orders/paginate.ts", 10, "const start = page * size;")
    verified, _ = checks.verify_findings(files, [review("correctness", off_by_two)])
    assert verified[0]["line"] == 12 and verified[0]["lineCorrected"] and verified[0]["citedLine"] == 10


def test_verify_holds_quotes_from_context_lines():
    files = files_for("PR-101")
    context = finding("app/routes/users.py", 11, 'row = conn.execute("SELECT id, name FROM users WHERE id = ?", (user_id,)).fetchone()')
    verified, held = checks.verify_findings(files, [review("security", context)])
    assert not verified and len(held) == 1


def test_duplicate_findings_merge_and_keep_highest_severity():
    files = files_for("PR-101")
    q = "f\"SELECT id, name FROM users WHERE name LIKE '%{name}%' ORDER BY name\""
    a = finding("app/routes/users.py", 23, q, "medium")
    b = finding("app/routes/users.py", 23, q, "high")
    verified, _ = checks.verify_findings(files, [review("correctness", a), review("security", b)])
    assert len(verified) == 1
    assert verified[0]["severity"] == "high" and set(verified[0]["reviewers"]) == {"correctness", "security"}


def test_verdict_comes_from_verified_severities():
    assert checks.decide([{"severity": "high"}])[0] == "request_changes"
    assert checks.decide([{"severity": "medium"}, {"severity": "low"}])[0] == "comment"
    assert checks.decide([{"severity": "low"}])[0] == "approve"
    assert checks.decide([])[0] == "approve"


def test_graph_skips_reviewers_for_docs_only_change():
    case = {"id": "X", "title": "Docs", "description": "", "author": "a", "language": "md", "teaser": "",
            "diff": "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n"}
    final = GRAPH.invoke({"case": case, "mode": "replay", "reviews": [], "trace": []})
    assert final["plan"] == [] and final["verdict"] == "approve"
    assert [t["node"] for t in final["trace"]] == ["parse_diff", "plan_review", "verify", "decide"]
