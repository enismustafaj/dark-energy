"""Shared base for LLM phrasing backends (Claude, OpenAI).

Owns everything provider-agnostic: prompt construction, JSON parsing, the
grounding post-check, and the per-fact fallback to the deterministic template
phraser. A concrete backend only implements ``_complete()`` — the single call
that sends messages to its provider and returns raw text.
"""

from __future__ import annotations

import json

from ..models import FactBundle, PhrasedInsight
from . import prompts
from .template_phraser import ACTION_LABELS, TemplatePhraser


class LLMPhraser:
    name = "llm"

    def __init__(self) -> None:
        self._fallback = TemplatePhraser()

    def _complete(self, system: str, user: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def phrase(self, bundle: FactBundle) -> list[PhrasedInsight]:
        if not bundle.facts:
            return []
        fallback = self._fallback.phrase(bundle)
        fb_by_key = {p.fact_key: p for p in fallback}

        try:
            raw = self._complete(prompts.SYSTEM_PROMPT, prompts.build_user_prompt(bundle))
            generated = self._parse(raw)
        except Exception:
            return fallback  # provider/parse failure → fully deterministic output

        out: list[PhrasedInsight] = []
        for fact in bundle.facts:
            gen = generated.get(fact.key)
            fb = fb_by_key.get(fact.key)
            action_label = ACTION_LABELS.get(fact.suggested_action_key or "")
            if gen is None:
                out.append(fb if fb else PhrasedInsight(
                    fact_key=fact.key, title=fact.title, body=fact.detail,
                    action_label=action_label))
                continue
            text = f"{gen.get('title','')} {gen.get('body','')}"
            # Grounding check: any number not permitted by the fact => reject this
            # one and use the template phrasing instead.
            if prompts.grounding_violations(text, fact.numbers):
                out.append(fb if fb else PhrasedInsight(
                    fact_key=fact.key, title=fact.title, body=fact.detail,
                    action_label=action_label))
                continue
            out.append(PhrasedInsight(
                fact_key=fact.key,
                title=gen.get("title", fact.title),
                body=gen.get("body", fact.detail),
                action_label=action_label,
            ))
        return out

    @staticmethod
    def _parse(raw: str) -> dict[str, dict]:
        data = json.loads(raw)
        items = data.get("insights", data if isinstance(data, list) else [])
        return {it["fact_key"]: it for it in items if "fact_key" in it}
