"""Export recorded runs to the portfolio site, so its Replay mode works without this service running.

  uv run python scripts/export_site.py [--site ../healthcare-payer-ai-portfolio]

Writes data/code-review/results/<PR>.json and data/code-review/eval-summary.json in the site."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.code_review.cases import load_cases  # noqa: E402
from app.code_review.graph import run_review  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=str(ROOT.parent / "healthcare-payer-ai-portfolio"))
    args = ap.parse_args()
    site = Path(args.site)
    if not (site / "package.json").exists():
        sys.exit(f"{site} doesn't look like the site project.")

    out = site / "data" / "code-review" / "results"
    out.mkdir(parents=True, exist_ok=True)
    for case in load_cases():
        result = run_review(case, "replay")
        result.pop("generatedAt", None)
        (out / f"{case['id']}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out / (case['id'] + '.json')}")

    summary = ROOT / "evals" / "code-review-summary.json"
    if summary.exists():
        shutil.copy(summary, site / "data" / "code-review" / "eval-summary.json")
        print("Copied eval summary")
    else:
        print("No live eval summary yet; run the eval with --live first.")


if __name__ == "__main__":
    main()
