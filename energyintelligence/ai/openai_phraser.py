"""OpenAI phrasing backend.

Sibling of ``ClaudePhraser`` behind the same ``LLMPhraser`` base — only the
provider call differs. Uses chat-completions JSON mode for structured output;
the shared base applies the identical number-grounding post-check, so the
"LLM never invents a number" guarantee holds across providers.
"""

from __future__ import annotations

import os

from ..config import get_settings
from .llm_phraser import LLMPhraser


class OpenAIPhraser(LLMPhraser):
    name = "openai"

    def __init__(self) -> None:
        super().__init__()
        import openai  # raises if SDK absent -> registry falls back to template

        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY not set")
        self._client = openai.OpenAI()
        self._model = get_settings().openai_model

    def _complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        return resp.choices[0].message.content or "{}"
