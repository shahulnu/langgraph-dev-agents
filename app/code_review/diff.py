"""Unified diff parsing. Plain code, no model: every later check depends on knowing exactly
which lines a pull request added and what their new line numbers are."""

from __future__ import annotations

import re
from typing import Literal, TypedDict

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class DiffLine(TypedDict):
    kind: Literal["add", "del", "ctx"]
    text: str
    oldNo: int | None
    newNo: int | None


class FileDiff(TypedDict):
    path: str
    status: Literal["added", "modified", "deleted"]
    lines: list[DiffLine]


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a git unified diff into files and numbered lines."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    old_no = new_no = 0

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            path = raw.split(" b/", 1)[1] if " b/" in raw else raw.split()[-1]
            current = {"path": path, "status": "modified", "lines": []}
            files.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current["status"] = "added"
        elif raw.startswith("deleted file mode"):
            current["status"] = "deleted"
        elif raw.startswith(("--- ", "+++ ", "index ")):
            continue
        elif m := HUNK.match(raw):
            old_no, new_no = int(m.group(1)), int(m.group(2))
        elif raw.startswith("+"):
            current["lines"].append({"kind": "add", "text": raw[1:], "oldNo": None, "newNo": new_no})
            new_no += 1
        elif raw.startswith("-"):
            current["lines"].append({"kind": "del", "text": raw[1:], "oldNo": old_no, "newNo": None})
            old_no += 1
        elif raw.startswith(" ") or raw == "":
            current["lines"].append({"kind": "ctx", "text": raw[1:], "oldNo": old_no, "newNo": new_no})
            old_no += 1
            new_no += 1
    return files


def added_lines(files: list[FileDiff]) -> dict[str, dict[int, str]]:
    """Map each file path to {new line number: text} for the lines the pull request added."""
    return {
        f["path"]: {line["newNo"]: line["text"] for line in f["lines"] if line["kind"] == "add" and line["newNo"]}
        for f in files
    }


def render_for_review(files: list[FileDiff]) -> str:
    """Show the diff to a reviewer with new line numbers, marking added lines with '+'."""
    out: list[str] = []
    for f in files:
        out.append(f'<file path="{f["path"]}" status="{f["status"]}">')
        for line in f["lines"]:
            if line["kind"] == "del":
                out.append(f"-      | {line['text']}")
            else:
                mark = "+" if line["kind"] == "add" else " "
                out.append(f"{mark}{line['newNo']:>5} | {line['text']}")
        out.append("</file>")
    return "\n".join(out)
