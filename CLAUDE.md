# LangGraph dev agents (Python)

Developer-tools agents for Shahul Ummarali's portfolio, built with LangGraph and LangChain on Claude. The portfolio
site is `../healthcare-payer-ai-portfolio`; it shows these agents in its "Developer tools" section and serves recorded
runs exported from here. README.md has the design, commands and API.

## Conventions
- Python via `uv` (`uv sync`, `uv run ...`). Model `claude-opus-5-5` through `langchain_anthropic.ChatAnthropic`.
- Structured output must use `method="json_schema"`: the default `function_calling` forces a tool call, which Claude
  Opus 5.5 rejects. Keep the refusal fallback (`betas=["server-side-fallback-2026-07-01"]`,
  `model_kwargs={"fallbacks": "default"}`) and the cached system prompt.
- Same rules as the healthcare demos: code makes decisions (routing, verification, verdict); the model reads and
  explains. A finding counts only if its quoted code is on an added line. If the eval fails, tighten the prompt, not the check.
- Only `--live` eval runs write `evals/code-review-summary.json`. `--live --record` also rewrites `data/code-review/recorded.json`.
- After re-recording, run `uv run python scripts/export_site.py` so the site's replay data and numbers match.
- Synthetic data only. Diffs live in `data/code-review/diffs/*.diff`; line numbers in `evals/code-review-labels.json` are
  new-file line numbers (check them with the parser, not by counting).

## Standing rules from the owner
- Don't commit or push to git until asked.
- Never print or echo the API key. It's in `.env` here (git-ignored) and in the site's `.env.local`; a rotated key must be updated in both.
- Stop the uvicorn service when done.
