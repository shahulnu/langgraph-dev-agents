"""The synthetic pull requests used by the demo and the eval."""

import json
from functools import lru_cache

from app import settings

CASES_DIR = settings.DATA_DIR / "code-review"


@lru_cache(maxsize=1)
def load_cases() -> list[dict]:
    cases = json.loads((CASES_DIR / "cases.json").read_text(encoding="utf-8"))
    for c in cases:
        c["diff"] = (CASES_DIR / c["diffFile"]).read_text(encoding="utf-8")
    return cases


def find_case(case_id: str) -> dict | None:
    return next((c for c in load_cases() if c["id"] == case_id), None)
