"""Score the Incident Debugger against labeled incidents.

  uv run python scripts/eval_incident.py                  # replay recorded runs (free; still runs the tests)
  uv run python scripts/eval_incident.py --live           # call Claude (billed)
  uv run python scripts/eval_incident.py --live --record  # call Claude and save the runs for replay

Only live runs write evals/incident-summary.json, the numbers the site shows."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import settings  # noqa: E402
from app.incident.agents import RECORDED_PATH  # noqa: E402
from app.incident.graph import investigate, to_recording  # noqa: E402
from app.incident.world import list_incidents  # noqa: E402

LABELS = json.loads((ROOT / "evals" / "incident-labels.json").read_text(encoding="utf-8"))
SUMMARY_PATH = ROOT / "evals" / "incident-summary.json"


def score(inc_id: str, r: dict) -> dict:
    lab = LABELS["incidents"][inc_id]
    rep = r["report"]
    commit = (rep.get("suspected_commit") or "")[:7] or None
    loc_ok = True
    if lab["location"]:
        loc = rep.get("suspected_location") or ""
        path, _, line = loc.rpartition(":")
        loc_ok = path == lab["location"]["file"] and line.isdigit() and lab["location"]["lines"][0] <= int(line) <= lab["location"]["lines"][1]
    cause_ok = rep.get("caused_by_deploy") == lab["caused_by_deploy"] and (commit == lab["commit"] if lab["commit"] else True) and loc_ok
    cited = [e for f in r["findings"] for e in f["evidence"]] + r["report"]["evidence"]
    return {
        "incident": inc_id,
        "verdict": r["verdict"],
        "verdictOk": r["verdict"] == lab["verdict"],
        "causeOk": cause_ok,
        "commit": commit,
        "location": rep.get("suspected_location"),
        "redHerringBlamed": commit == LABELS["red_herring_commit"] or (not lab["caused_by_deploy"] and bool(rep.get("caused_by_deploy"))),
        "evidence": len(cited),
        "evidenceVerified": sum(e["verified"] for e in cited),
        "delegations": r["delegations"],
        "path": " > ".join(s["specialist"] or "conclude" for s in r["steps"]),
        "cost": (r["usage"] or {}).get("costUsd", 0),
        "seconds": round(r["ms"] / 1000),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()
    if args.record and not args.live:
        sys.exit("--record needs --live.")
    if args.live and not settings.live_available():
        sys.exit("Set ANTHROPIC_API_KEY (in .env) for live runs.")
    mode = "live" if args.live else "replay"

    rows, rec = [], {"source": "live", "model": settings.MODEL, "recordedAt": None, "cases": {}}
    for inc in list_incidents():
        r = await investigate(inc["id"], mode)
        rows.append(score(inc["id"], r))
        rec["cases"][inc["id"]] = to_recording(r)
        x = rows[-1]
        print(f"{x['incident']}  verdict {x['verdict']:<21}{'ok ' if x['verdictOk'] else 'BAD'}  cause {'ok ' if x['causeOk'] else 'BAD'} "
              f"({x['commit']}, {x['location']})  evidence {x['evidenceVerified']}/{x['evidence']}  red herring blamed {x['redHerringBlamed']}  "
              f"delegations {x['delegations']}  ${x['cost']:.3f}  {x['seconds']}s")
        print(f"    path: {x['path']}")

    ev = sum(r["evidence"] for r in rows)
    summary = {
        "mode": mode,
        "model": settings.MODEL,
        "runAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "incidents": len(rows),
        "rootCauseAccuracy": sum(r["causeOk"] for r in rows) / len(rows),
        "verdictAccuracy": sum(r["verdictOk"] for r in rows) / len(rows),
        "evidenceVerifiedRate": sum(r["evidenceVerified"] for r in rows) / ev if ev else None,
        "redHerringsBlamed": sum(r["redHerringBlamed"] for r in rows),
        "avgDelegations": round(sum(r["delegations"] for r in rows) / len(rows), 1),
        "totalCostUsd": round(sum(r["cost"] for r in rows), 4),
    }
    print(f"\n{mode.upper()}  root cause {summary['rootCauseAccuracy']:.0%}  verdicts {summary['verdictAccuracy']:.0%}  "
          f"evidence verified {summary['evidenceVerifiedRate']:.0%}  red herrings blamed {summary['redHerringsBlamed']}  "
          f"avg delegations {summary['avgDelegations']}  cost ${summary['totalCostUsd']:.4f}")
    if mode == "live":
        SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {SUMMARY_PATH.relative_to(ROOT)}")
    if args.record:
        rec["recordedAt"] = summary["runAt"]
        RECORDED_PATH.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {RECORDED_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
