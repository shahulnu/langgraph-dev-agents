"""Runs a model-written reproduction test against both service versions, safely.

Two layers: a static check of the test's syntax tree (what it may import and call), then a run in a
subprocess with a timeout, against a temporary copy of each version. The proof is a test that fails
on the deployed version and passes on the previous one."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time

from app.incident.world import DEPLOYED, PREVIOUS, SERVICE

ALLOWED_IMPORTS = {"pytest", "checkout", "math", "decimal"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals", "vars",
                   "getattr", "setattr", "delattr", "breakpoint", "help", "exit", "quit", "memoryview"}
MAX_LINES = 120
TIMEOUT_SECONDS = 30


def check_test(code: str) -> list[str]:
    """Return the reasons the test can't be run; empty means it passed the check."""
    problems: list[str] = []
    if len(code.splitlines()) > MAX_LINES:
        problems.append(f"Longer than {MAX_LINES} lines.")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e.msg} (line {e.lineno})."]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    problems.append(f"Imports {a.name}; only {', '.join(sorted(ALLOWED_IMPORTS))} are allowed.")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS or node.level:
                problems.append(f"Imports from {node.module}; only {', '.join(sorted(ALLOWED_IMPORTS))} are allowed.")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"Uses {node.id}(), which isn't allowed.")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"Uses the dunder attribute {node.attr}.")
    if not any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in tree.body):
        problems.append("Defines no test_ function.")
    return sorted(set(problems))


def _run(code: str, version: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="repro-") as tmp:
        shutil.copytree(SERVICE / version / "checkout", os.path.join(tmp, "checkout"))
        with open(os.path.join(tmp, "test_repro.py"), "w", encoding="utf-8") as f:
            f.write(code)
        env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PYTHONPATH": tmp, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
        t0 = time.perf_counter()
        try:
            p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:warnings", "test_repro.py"],
                               cwd=tmp, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
            code_, out = p.returncode, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired:
            code_, out = -1, f"Timed out after {TIMEOUT_SECONDS} s."
        ms = round((time.perf_counter() - t0) * 1000)
    outcome = {0: "passed", 1: "failed"}.get(code_, "error")
    tail = "\n".join(l for l in out.strip().splitlines() if l.strip())[-1200:]
    return {"version": version, "outcome": outcome, "output": tail.replace(tmp, "<tmp>"), "ms": ms}


def run_on_both(code: str) -> dict:
    problems = check_test(code)
    if problems:
        return {"ran": False, "problems": problems, "runs": [], "proves": False}
    runs = [_run(code, PREVIOUS), _run(code, DEPLOYED)]
    proves = runs[0]["outcome"] == "passed" and runs[1]["outcome"] == "failed"
    return {"ran": True, "problems": [], "runs": runs, "proves": proves}
