"""Prompt construction + the grounding contract shared by all LLM phrasers."""

from __future__ import annotations

import json
import re

from ..models import FactBundle

SYSTEM_PROMPT = (
    "You are the customer-facing voice of a home-energy assistant. You are given "
    "a JSON bundle of pre-computed FACTS about one household. Your ONLY job is to "
    "rephrase each fact into a short, warm, plain-language insight the homeowner can "
    "act on.\n\n"
    "HARD RULES:\n"
    "1. Use ONLY the numbers provided in each fact's `numbers` object. Never compute, "
    "round differently, estimate, or invent any number — not even units conversions.\n"
    "2. Do not add facts, savings figures, or claims that are not in the input.\n"
    "3. Keep each insight to a title (<=8 words) and a body (1-2 sentences).\n"
    "4. Return STRICT JSON: an object {\"insights\": [{\"fact_key\", \"title\", \"body\"}]} "
    "with one entry per input fact, preserving fact_key.\n"
)


def build_user_prompt(bundle: FactBundle) -> str:
    payload = {
        "household": bundle.context,
        "facts": [
            {
                "fact_key": f.key,
                "type": f.type,
                "severity": f.severity,
                "period": f.period,
                "numbers": f.numbers,
                "hint": f.detail,
            }
            for f in bundle.facts
        ],
    }
    return (
        "Rephrase these facts. Allowed numbers per fact are exactly the values in its "
        "`numbers` object.\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    )


# A minus sign only counts when not preceded by a digit (so "2025-08" yields
# 2025 and 8, not 2025 and -8 — the hyphen is a date separator there).
_NUM_RE = re.compile(r"(?<!\d)-?\d+(?:[.,]\d+)?")
# Clock times "HH:MM" — the minutes are a time separator, not a standalone number.
_CLOCK_RE = re.compile(r"(\d{1,2}):\d{2}")


def _numbers_in(text: str) -> set[str]:
    """Normalised numeric tokens found in free text (commas → dots, trim zeros)."""
    # Collapse clock times to just the hour so "13:00" → "13", not "13" and "0".
    text = _CLOCK_RE.sub(r"\1", text or "")
    found = set()
    for tok in _NUM_RE.findall(text):
        norm = tok.replace(",", ".")
        try:
            found.add(_canon(float(norm)))
        except ValueError:
            continue
    return found


def _canon(x: float) -> str:
    # Canonical form so 24 == 24.0 == 24.00.
    if x == int(x):
        return str(int(x))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def allowed_numbers(fact_numbers: dict) -> set[str]:
    """The set of numeric tokens a phrasing of this fact may legitimately contain,
    including common sub-tokens (e.g. a date period's parts, hour 13 -> '13')."""
    allowed: set[str] = set()
    for v in fact_numbers.values():
        if isinstance(v, (int, float)):
            allowed.add(_canon(float(v)))
        elif isinstance(v, str):
            allowed |= _numbers_in(v)  # e.g. "2025-08" -> 2025, 8
    return allowed


def grounding_violations(text: str, fact_numbers: dict) -> set[str]:
    """Numbers in the generated text that are NOT permitted by the fact. A
    non-empty result means the phrasing hallucinated a figure → reject it."""
    return _numbers_in(text) - allowed_numbers(fact_numbers)
