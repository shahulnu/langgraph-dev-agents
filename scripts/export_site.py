"""Export recorded runs to the portfolio site, so its Replay mode works without this service running.

  uv run python scripts/export_site.py [--site ../ai-portfolio]

Writes the Code Review Agent results (data/code-review) and Incident Debugger results (data/incident)
in the site, with their eval summaries."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.code_review.cases import load_cases  # noqa: E402
from app.code_review.graph import run_review  # noqa: E402
from app.incident.graph import investigate  # noqa: E402
from app.incident.world import list_incidents  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=str(ROOT.parent / "ai-portfolio"))
    args = ap.parse_args()
    site = Path(args.site)
    if not (site / "package.json").exists():
        sys.exit(f"{site} doesn't look like the site project.")

    out = site / "data" / "code-review" / "results"
    out.mkdir(parents=True, exist_ok=True)
    for case in load_cases():
        result = run_review(case, "replay")
        result.pop("generatedAt", None)
        for t in result["trace"]:
            t["ms"] = 0  # timings change on every run; keep re-exports stable
        (out / f"{case['id']}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out / (case['id'] + '.json')}")

    summary = ROOT / "evals" / "code-review-summary.json"
    if summary.exists():
        shutil.copy(summary, site / "data" / "code-review" / "eval-summary.json")
        print("Copied eval summary")
    else:
        print("No live eval summary yet; run the eval with --live first.")

    # Incident Debugger: replays still run every tool call and the reproduction tests.
    out = site / "data" / "incident" / "results"
    out.mkdir(parents=True, exist_ok=True)
    for inc in list_incidents():
        result = asyncio.run(investigate(inc["id"], "replay"))
        for key in ("generatedAt", "ms"):
            result.pop(key, None)
        for s in result["steps"]:
            s["ms"] = 0
        for f in result["findings"]:
            f["ms"] = 0
            for run in (f.get("repro") or {}).get("runs", []):
                run["ms"] = 0
        (out / f"{inc['id']}.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {out / (inc['id'] + '.json')}")
    summary = ROOT / "evals" / "incident-summary.json"
    if summary.exists():
        shutil.copy(summary, site / "data" / "incident" / "eval-summary.json")
        print("Copied incident eval summary")


if __name__ == "__main__":
    main()
