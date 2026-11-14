# LangGraph dev agents

AI agents for engineering teams, built with **LangGraph** and **LangChain** on Claude. Part of
[Shahul Ummarali's AI portfolio](https://github.com/shahulnu/healthcare-payer-ai-portfolio): the site shows these agents
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
