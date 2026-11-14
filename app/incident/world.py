"""The synthetic world an incident is investigated in: logs, metrics, notes, commit history and the
two service versions. Every function here is read-only, and every result carries IDs that the
evidence check can verify later."""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

from app import settings

BASE = settings.DATA_DIR / "incident"
SERVICE = BASE / "service"
VERSIONS = ("v1.4.1", "v1.4.2")
DEPLOYED, PREVIOUS = "v1.4.2", "v1.4.1"


@lru_cache(maxsize=1)
def history() -> dict:
    return json.loads((BASE / "history.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def incident(incident_id: str) -> dict:
    return json.loads((BASE / "incidents" / f"{incident_id}.json").read_text(encoding="utf-8"))


def list_incidents() -> list[dict]:
    return [{k: incident(p.stem)[k] for k in ("id", "title", "teaser", "opened", "description")} for p in sorted((BASE / "incidents").glob("INC-*.json"))]


def _file(version: str, path: str) -> Path | None:
    if version not in VERSIONS:
        return None
    p = (SERVICE / version / path).resolve()
    root = (SERVICE / version).resolve()
    return p if p.is_file() and root in p.parents else None


def code_files(version: str) -> list[str]:
    root = SERVICE / version
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.py"))


# ---------- tools ----------

def search_logs(inc: dict, query: str = "", level: str | None = None, since: str | None = None, until: str | None = None, limit: int = 25) -> str:
    q = query.lower()
    hits = [
        l for l in inc["logs"]
        if (not q or q in l["message"].lower() or q in l["logger"].lower())
        and (not level or l["level"] == level.upper())
        and (not since or l["ts"] >= since)
        and (not until or l["ts"] <= until)
    ]
    shown = hits[: max(1, min(limit, 40))]
    lines = [f"{l['id']} {l['ts']} {l['level']} {l['logger']} {l['message']}" for l in shown]
    return f"{len(hits)} matching line(s); showing {len(shown)}.\n" + "\n".join(lines)


def error_summary(inc: dict) -> str:
    deploy = history()["deploys"][-1]["at"]
    buckets: dict[tuple[str, str], list[str]] = {}
    for l in inc["logs"]:
        if l["level"] in ("ERROR", "WARN"):
            sig = re.sub(r"order=O-\d+", "order=*", l["message"])[:90]
            buckets.setdefault((l["level"], sig), []).append(l)
    out = [f"Deploy of {DEPLOYED} at {deploy}. ERROR/WARN messages, counted before and after it:"]
    for (level, sig), ls in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        before = sum(l["ts"] < deploy for l in ls)
        out.append(f"- {level} x{len(ls)} (before {before}, after {len(ls) - before}), first {ls[0]['ts']} ({ls[0]['id']}): {sig}")
    return "\n".join(out) if len(out) > 1 else "No ERROR or WARN lines."


def get_metrics(inc: dict, name: str) -> str:
    series = inc["metrics"].get(name)
    if series is None:
        return f"Unknown metric. Available: {', '.join(inc['metrics'])}"
    out = [f"{name}, 10-minute averages:"]
    for i in range(0, len(series), 10):
        chunk = series[i : i + 10]
        out.append(f"- {chunk[0]['ts'][11:16]}–{chunk[-1]['ts'][11:16]} UTC: {sum(p['value'] for p in chunk) / len(chunk):.2f}")
    return "\n".join(out)


def get_deploys() -> str:
    return "\n".join(f"- {d['version']} deployed {d['at']}; commits: {', '.join(d['commits']) or 'n/a'}" for d in history()["deploys"])


def get_notes(inc: dict) -> str:
    return "\n".join(f"{n['id']} {n['ts']} [{n['source']}] {n['text']}" for n in inc["notes"]) or "No notes."


def list_commits() -> str:
    return "\n".join(f"{c['sha']} {c['at']} {c['author']}: {c['title']} (files: {', '.join(c['files'])})" for c in history()["commits"])


def show_diff(sha: str) -> str:
    c = next((c for c in history()["commits"] if c["sha"] == sha.strip()[:7]), None)
    if c is None:
        return f"No commit {sha}. Use list_commits."
    out = [f"commit {c['sha']} {c['title']}"]
    for path in c["files"]:
        old = _file(PREVIOUS, path).read_text(encoding="utf-8").splitlines()
        new = _file(DEPLOYED, path).read_text(encoding="utf-8").splitlines()
        out += list(difflib.unified_diff(old, new, f"a/{path}", f"b/{path}", lineterm="", n=2))
    return "\n".join(out)


def read_file(path: str, version: str = DEPLOYED, start: int = 1, end: int = 200) -> str:
    f = _file(version, path)
    if f is None:
        return f"No file {path} in {version}. Files: {', '.join(code_files(DEPLOYED))}"
    lines = f.read_text(encoding="utf-8").splitlines()
    return f"{path} @ {version}\n" + "\n".join(f"{n:>4}  {lines[n - 1]}" for n in range(max(1, start), min(len(lines), end) + 1))


def grep_code(pattern: str, version: str = DEPLOYED) -> str:
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
    hits = []
    for path in code_files(version):
        for n, line in enumerate(_file(version, path).read_text(encoding="utf-8").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{path}:{n}: {line.strip()}")
    return "\n".join(hits[:40]) or "No matches."


# ---------- evidence check ----------

REF = re.compile(r"^(log|commit|code|metric|note|deploy|test):(.+)$")


def verify_ref(inc: dict, ref: str) -> bool:
    """True if an evidence reference points at something that exists."""
    m = REF.match(ref.strip())
    if not m:
        return False
    kind, val = m.group(1), m.group(2).strip()
    if kind == "log":
        return any(l["id"] == val for l in inc["logs"])
    if kind == "commit":
        return any(c["sha"] == val[:7] for c in history()["commits"])
    if kind == "metric":
        return val in inc["metrics"]
    if kind == "note":
        return any(n["id"] == val for n in inc["notes"])
    if kind == "deploy":
        return any(d["version"] == val for d in history()["deploys"])
    if kind == "test":
        return val == "repro"
    # code:<path>:<line>[@<version>]
    cm = re.match(r"^(.+?):(\d+)(?:@(v[\d.]+))?$", val)
    if not cm:
        return False
    f = _file(cm.group(3) or DEPLOYED, cm.group(1))
    return bool(f) and 1 <= int(cm.group(2)) <= len(f.read_text(encoding="utf-8").splitlines())
