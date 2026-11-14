"""The deterministic layer. Code decides which reviewers run, which findings are real and the
verdict. The model's findings count only when their quoted code is on an added line of the diff."""

from __future__ import annotations

import re

from app.code_review.diff import added_lines

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

# Signals that a change needs the security reviewer.
SECURITY_SIGNALS = re.compile(
    r"\b(select|insert|update|delete)\b.*\b(from|into|set|where)\b|execute\(|subprocess|os\.system|eval\(|exec\(|"
    r"pickle|yaml\.load|password|secret|token|api[_-]?key|auth|request\.(args|form|json|params|query|body)|"
    r"req\.(query|body|params)|innerHTML|dangerouslySetInnerHTML",
    re.IGNORECASE,
)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec|test_)[^/]*$", re.IGNORECASE)
CODE_EXT = re.compile(r"\.(py|ts|tsx|js|jsx|java|go|rb|cs|kt)$")


def plan_reviewers(files: list[dict]) -> list[dict]:
    """Choose which specialist reviewers to run, and say why."""
    code_files = [f for f in files if CODE_EXT.search(f["path"]) and f["status"] != "deleted"]
    if not code_files:
        return []
    source = [f for f in code_files if not TEST_PATH.search(f["path"])]
    tests = [f for f in code_files if TEST_PATH.search(f["path"])]
    added_text = "\n".join(l["text"] for f in code_files for l in f["lines"] if l["kind"] == "add")

    plan = [{"reviewer": "correctness", "reason": f"{len(code_files)} code file(s) changed."}]
    if m := SECURITY_SIGNALS.search(added_text):
        plan.append({"reviewer": "security", "reason": f'Added code touches a security-sensitive pattern ("{m.group(0).strip()[:40]}").'})
    if source:
        plan.append({
            "reviewer": "tests",
            "reason": f"{len(source)} source file(s) changed with {len(tests)} test file(s) in the diff.",
        })
    return plan


def _norm(s: str) -> str:
    return " ".join(s.split())


def verify_findings(files: list[dict], reviews: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep findings whose quote is on an added line; hold the rest. Then merge duplicates."""
    added = added_lines(files)
    verified: list[dict] = []
    held: list[dict] = []

    for review in reviews:
        for f in review["output"]["findings"]:
            finding = {**f, "category": review["reviewer"], "reviewers": [review["reviewer"]], "lineCorrected": False}
            lines = added.get(f["file"])
            quote = _norm(f["quote"])
            if not lines:
                held.append({**finding, "reason": "The file isn't in the diff."})
            elif not quote:
                held.append({**finding, "reason": "The finding has no quoted code."})
            elif quote in _norm(lines.get(f["line"], "")):
                verified.append(finding)
            else:
                matches = [n for n, text in lines.items() if quote in _norm(text)]
                if len(matches) == 1:
                    verified.append({**finding, "line": matches[0], "lineCorrected": True, "citedLine": f["line"]})
                else:
                    held.append({**finding, "reason": "The quoted code isn't on an added line of this file."})

    # Two reviewers flagging the same line: keep the most severe finding and credit both.
    verified.sort(key=lambda x: (SEVERITY_RANK[x["severity"]], x["file"], x["line"]))
    merged: list[dict] = []
    for f in verified:
        same = next((m for m in merged if m["file"] == f["file"] and m["line"] == f["line"]), None)
        if same:
            if f["category"] not in same["reviewers"]:
                same["reviewers"].append(f["category"])
        else:
            merged.append(f)
    merged.sort(key=lambda x: (SEVERITY_RANK[x["severity"]], x["file"], x["line"]))
    for i, f in enumerate(merged, 1):
        f["id"] = f"F{i}"
    return merged, held


def decide(findings: list[dict]) -> tuple[str, str]:
    """Suggested verdict from verified findings only. A person makes the merge decision."""
    high = sum(f["severity"] == "high" for f in findings)
    medium = sum(f["severity"] == "medium" for f in findings)
    low = sum(f["severity"] == "low" for f in findings)
    if high:
        return "request_changes", f"{high} high-severity finding(s) should be fixed before merge."
    if medium:
        return "comment", f"No high-severity findings; {medium} medium-severity finding(s) to address."
    if low:
        return "approve", f"No high or medium findings; {low} low-severity note(s)."
    return "approve", "No verified findings."


def compose_comment(findings: list[dict], verdict: str, reason: str) -> str:
    """The review comment, written by code from verified findings, so it can't add claims of its own."""
    label = {"request_changes": "Changes requested", "comment": "Comments", "approve": "Looks good"}[verdict]
    lines = [f"{label}: {reason}"]
    for f in findings:
        lines.append(f"- [{f['severity']}] {f['file']}:{f['line']} {f['title']}. {f['explanation']} Suggestion: {f['suggestion']}")
    lines.append("Suggested by an automated review. A maintainer makes the merge decision.")
    return "\n".join(lines)
