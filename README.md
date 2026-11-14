# LangGraph dev agents

AI agents for engineering teams, built with **LangGraph** and **LangChain** on Claude: a Code Review Agent with parallel reviewers, and an Incident Debugger with an orchestrator and specialist agents. Part of
[Shahul Ummarali's AI portfolio](https://github.com/shahulnu/ai-portfolio): the site shows these agents
in its Developer tools section.

## Code Review Agent

Reviews a pull request with specialist reviewers that run in parallel, checks every finding against the diff, and
suggests a verdict. A maintainer makes the merge decision.

```
parse_diff ─► plan_review ─┬─► review: correctness ─┐
                           ├─► review: security    ├─► verify ─► decide
                           └─► review: tests       ─┘
```

| Step | Who | What it does |
|---|---|---|
| `parse_diff` | Code | Parses the unified diff into files and numbered lines. |
| `plan_review` | Code | Picks reviewers: correctness always, security only when added code touches SQL, shell, secrets, auth or user input, tests when source files change. |
| `review` | Claude via LangChain | One parallel branch per reviewer (LangGraph `Send`). Structured output: file, line, exact quoted code, severity, explanation, fix. |
| `verify` | Code | Keeps a finding only if its quote is on an added line of that file; corrects a wrong line number when the quote matches one line; merges duplicates. |
| `decide` | Code | Suggested verdict from verified severities (high → request changes, medium → comment, otherwise approve) and a review comment built from verified findings only. |

Model calls use `ChatAnthropic` with `claude-opus-5-5`, `with_structured_output(..., method="json_schema")`
(Claude's structured outputs; forced tool calls aren't supported on this model), a cached system prompt per reviewer,
and the server-side refusal fallback (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks: "default"`).

The diff is treated as data: one test pull request contains a comment telling AI reviewers to approve it. The
reviewers report it, and the verdict still requests changes.

### Results

`uv run python scripts/eval_code_review.py --live` against three labeled synthetic pull requests (one SQL injection,
one off-by-one bug with a weak test and a planted instruction, one clean refactor):

| Required findings | False positives | Verdicts | Quotes verified | Planted instruction | Cost |
|---|---|---|---|---|---|
| 4/4 | 0 | 3/3 | 100% | resisted | $0.11 |

Three synthetic pull requests show that the pipeline works; they aren't a benchmark. See `evals/code-review-summary.json`.

## Incident Debugger

A multi-agent investigation of a production incident. An **orchestrator** delegates to specialists, reads what they
report, and changes course as evidence comes in. Code accepts a root cause as **confirmed** only when a test fails on the
deployed version and passes on the version before it.

```
orchestrator ──Command(goto)──► delegate ─► specialist subgraph (own tools and tool loop) ─┐
     ▲                                                                                    │
     └────────────────────────── case file (verified evidence) ◄─────────────────────────┘
orchestrator ──conclude or budget──► report ─► verdict (code)
```

| Agent or step | Who | What it does |
|---|---|---|
| Orchestrator | Claude | Each turn: delegate one task or conclude (structured output; LangGraph routes with `Command(goto)`). |
| Log analyst | Claude + tools | `error_summary`, `search_logs`, `get_notes` |
| Metrics analyst | Claude + tools | `get_metrics`, `get_deploys`, `get_notes` |
| Change investigator | Claude + tools | `list_commits`, `show_diff`, `get_deploys` |
| Code reader | Claude + tools | `read_file`, `grep_code` on v1.4.1 or v1.4.2 |
| Reproducer | Claude writes, code runs | One pytest test, checked (allowed imports only; no files, network, `eval` or dunder access), then run in a subprocess with a timeout on copies of both versions. |
| Budget, evidence, verdict | Code | At most 8 delegations. Every cited log line, commit, `file:line`, metric and note must exist, or it's dropped before the orchestrator sees it. Verdict: confirmed, likely, not caused by the deploy, or inconclusive. |

The synthetic world (`data/incident`) is a small checkout service in two versions, a three-commit deploy, and logs,
metrics and notes for three incidents: a `KeyError` on the gift-order path, wrong totals with no errors at all, and a
vendor payment outage that started 18 minutes before the deploy. A log-format commit in the deploy is a red herring.

**Results** (`uv run python scripts/eval_incident.py --live`):

| Root cause | Verdicts | Evidence verified | Red herrings blamed | Delegations per incident | Cost |
|---|---|---|---|---|---|
| 3/3 | 3/3 | 100% | 0 | 3.0 of 8 | $0.81 |

The first live run concluded the wrong-totals incident before the faulty line was cited, so the report described the
location in words; a prompt rule (pin the exact line before concluding) fixed it.

```bash
uv run python scripts/make_incidents.py                  # regenerate the incidents' logs, metrics and notes
uv run python scripts/eval_incident.py                   # replay (free; still runs tool calls and tests)
uv run python scripts/eval_incident.py --live --record   # live, and save the runs for replay (billed)
```

API: `GET /api/incident/incidents`, `GET /api/incident/investigate` (`{ liveAvailable }`),
`POST /api/incident/investigate` with `{ "incidentId": "INC-201", "mode": "replay" | "live" }`.

## Run it

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest                                              # unit tests, no model calls
uv run python scripts/eval_code_review.py                  # eval on recorded runs (free)
uv run python scripts/eval_code_review.py --live           # eval with live Claude calls (billed)
uv run python scripts/eval_code_review.py --live --record  # live, and save the runs for replay
uv run uvicorn app.main:app --port 8090                    # the HTTP service
```

Live calls need `ANTHROPIC_API_KEY` in `.env` (copy `.env.example`; `.env` is git-ignored). Without a key the service
serves recorded runs only. Live runs are capped per IP per day (`DAILY_LIMIT_PER_IP`, default 10).

### API

- `GET /api/code-review/cases`: the sample pull requests
- `GET /api/code-review/review`: `{ liveAvailable, engine }`
- `POST /api/code-review/review` with `{ "caseId": "PR-101", "mode": "replay" | "live" }`: the full review
- `GET /health`

### With the portfolio site

The site serves recorded runs on its own, so its public deployment needs no Python host. To refresh them after a new
recording, run this from this folder; it writes the results and eval summary into the site's `data/code-review`:

```bash
uv run python scripts/export_site.py
```

It exports both agents' recorded runs. For live Incident Debugger runs from the site, set `INCIDENT_ENGINE_URL=http://localhost:8090`.

For live reviews from the site, run the service and set `CODE_REVIEW_ENGINE_URL=http://localhost:8090` in the site's
`.env.local`.

## Layout

```
app/
  main.py               FastAPI service
  settings.py           model, limits, .env loading
  live_limit.py         per-IP daily cap on live runs
  code_review/
    graph.py            the LangGraph StateGraph
    reviewers.py        Claude calls through LangChain, and replay
    prompts.py          reviewer prompts
    checks.py           planning, quote verification, verdict
    diff.py             unified diff parsing
    models.py           schemas and graph state
data/code-review/       synthetic pull requests and recorded runs
evals/                  labels and the live eval summary
scripts/                eval and export to the site
tests/                  unit tests
```
