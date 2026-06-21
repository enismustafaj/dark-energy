"""Anthropic Claude phrasing backend.

Implements only the provider call; prompt construction, JSON parsing, and the
grounding post-check live in the shared ``LLMPhraser`` base. The LLM may only
rephrase the numbers already in each Fact — the base enforces that and falls
back to the template phraser on any violation.

Uses ``output_config.format`` (JSON schema) to force structured output. Note:
``temperature`` is intentionally omitted — it returns a 400 on Opus 4.8 / 4.7.
"""

from __future__ import annotations

import os

from ..config import get_settings
from .llm_phraser import LLMPhraser

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_key": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["fact_key", "title", "body"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["insights"],
    "additionalProperties": False,
}


class ClaudePhraser(LLMPhraser):
    name = "claude"

    def __init__(self) -> None:
        super().__init__()
        import anthropic  # raises if SDK absent -> registry falls back to template

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._client = anthropic.Anthropic()
        self._model = get_settings().claude_model

    def _complete(self, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}},
        )
        return next(b.text for b in resp.content if b.type == "text")
