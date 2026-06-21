"""The pluggable AI phrasing layer.

The backend computes every number deterministically (ETL, forecast, anomalies)
and packages them as a ``FactBundle``. A ``Phraser`` turns that into
user-friendly text and actions. The hard rule across every backend: a phraser
may only *rephrase* the numbers already in each ``Fact.numbers`` — it must never
introduce a figure of its own. The default ``TemplatePhraser`` enforces this
structurally; the LLM backends enforce it with a post-generation number check.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..config import get_settings
from ..models import FactBundle, PhrasedInsight


@runtime_checkable
class Phraser(Protocol):
    name: str

    def phrase(self, bundle: FactBundle) -> list[PhrasedInsight]:
        ...


def get_phraser(backend: str | None = None) -> Phraser:
    """Resolve the configured phraser. LLM backends fall back to the template
    phraser if their SDK / API key is unavailable, so the system always runs."""
    backend = (backend or get_settings().phraser_backend).lower()
    from .template_phraser import TemplatePhraser

    if backend == "template":
        return TemplatePhraser()
    if backend == "claude":
        try:
            from .claude_phraser import ClaudePhraser
            return ClaudePhraser()
        except Exception:
            return TemplatePhraser()
    if backend == "openai":
        try:
            from .openai_phraser import OpenAIPhraser
            return OpenAIPhraser()
        except Exception:
            return TemplatePhraser()
    return TemplatePhraser()
