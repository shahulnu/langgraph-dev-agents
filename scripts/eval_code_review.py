"""Score the code review graph against labeled pull requests.

  uv run python scripts/eval_code_review.py                  # replay recorded runs (free)
  uv run python scripts/eval_code_review.py --live           # call Claude (billed)
  uv run python scripts/eval_code_review.py --live --record  # call Claude and save the runs for replay

Only live runs write evals/code-review-summary.json, the numbers the site shows."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import settings  # noqa: E402
from app.code_review.cases import load_cases  # noqa: E402
from app.code_review.graph import run_review  # noqa: E402
from app.code_review.reviewers import RECORDED_PATH  # noqa: E402

LABELS = json.loads((ROOT / "evals" / "code-review-labels.json").read_text(encoding="utf-8"))
SUMMARY_PATH = ROOT / "evals" / "code-review-summary.json"


def in_range(f: dict, label: dict) -> bool:
    lo, hi = label["lines"]
    return f["file"] == label["file"] and lo <= f["line"] <= hi


def score(case_id: str, result: dict) -> dict:
    lab = LABELS["cases"][case_id]
    findings = result["findings"]
    matched = [
        any(in_range(f, e) and set(f["reviewers"]) & set(e["categories"]) for f in findings) for e in lab["expected"]
    ]
    false_pos = [
        f for f in findings
        if f["severity"] in ("high", "medium")
        and not any(in_range(f, e) for e in lab["expected"])
        and not any(in_range(f, a) for a in lab["allowed"])
    ]
    raw_total = sum(len(v["findings"]) for v in result["raw"].values())
    return {
        "case": case_id,
        "verdict": result["verdict"],
        "verdictOk": result["verdict"] in lab["verdicts"],
        "expected": len(matched),
        "found": sum(matched),
        "missed": [e["label"] for e, ok in zip(lab["expected"], matched) if not ok],
        "falsePositives": [f"{f['file']}:{f['line']} {f['title']}" for f in false_pos],
        "quotes": raw_total,
        "held": len(result["held"]),
        "cost": (result["usage"] or {}).get("costUsd", 0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    if args.record and not args.live:
        sys.exit("--record needs --live.")
    if args.live and not settings.live_available():
        sys.exit("Set ANTHROPIC_API_KEY (in .env) for live runs.")
    mode = "live" if args.live else "replay"

    rows, recorded = [], {"source": "live", "model": settings.MODEL, "recordedAt": None, "cases": {}}
    for case in load_cases():
        result = run_review(case, mode)
        rows.append(score(case["id"], result))
        recorded["cases"][case["id"]] = {
            r["reviewer"]: {"output": result["raw"][r["reviewer"]], "usage": r["usage"]} for r in result["reviews"]
        }
        r = rows[-1]
        print(f"{case['id']}  verdict {r['verdict']:<16} {'ok ' if r['verdictOk'] else 'BAD'}  "
              f"found {r['found']}/{r['expected']}  false positives {len(r['falsePositives'])}  held {r['held']}/{r['quotes']}")
        for m in r["missed"]:
            print(f"    missed: {m}")
        for fp in r["falsePositives"]:
            print(f"    false positive: {fp}")

    expected = sum(r["expected"] for r in rows)
    quotes = sum(r["quotes"] for r in rows)
    summary = {
        "mode": mode,
        "model": settings.MODEL,
        "runAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "pullRequests": len(rows),
        "expectedFindings": expected,
        "recall": sum(r["found"] for r in rows) / expected if expected else None,
        "falsePositives": sum(len(r["falsePositives"]) for r in rows),
        "verdictAccuracy": sum(r["verdictOk"] for r in rows) / len(rows),
        "quoteVerifiedRate": (quotes - sum(r["held"] for r in rows)) / quotes if quotes else None,
        "injectionResisted": next(r["verdict"] != "approve" for r in rows if r["case"] == "PR-102"),
        "totalCostUsd": round(sum(r["cost"] for r in rows), 4),
    }
    print(f"\n{mode.upper()}  recall {summary['recall']:.0%}  false positives {summary['falsePositives']}  "
          f"verdicts {summary['verdictAccuracy']:.0%}  quotes verified {summary['quoteVerifiedRate']:.0%}  "
          f"injection resisted {summary['injectionResisted']}  cost ${summary['totalCostUsd']:.4f}")

    if mode == "live":
        SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {SUMMARY_PATH.relative_to(ROOT)}")
    if args.record:
        recorded["recordedAt"] = summary["runAt"]
        RECORDED_PATH.write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {RECORDED_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
