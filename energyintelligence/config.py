"""Runtime configuration.

Everything is overridable via environment variables (prefix ``DARKENERGY_``)
so the same code can run against different datasets / scenarios without edits.
The provided dataset is treated as *one instance of a format*, never as a set
of hard assumptions — paths, the phraser backend, and simulator behaviour are
all settings, not constants.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DARKENERGY_", env_file=".env", extra="ignore")

    # --- storage ---
    db_path: Path = REPO_ROOT / "data.db"

    # --- dataset (the format instance to seed from) ---
    dataset_dir: Path = REPO_ROOT / "enpal-track-dataset"

    # --- AI phrasing layer ---
    # template = deterministic, no key. claude / openai = LLM rephrasing.
    phraser_backend: str = "template"
    claude_model: str = "claude-haiku-4-5"
    openai_model: str = "gpt-4o-mini"
    chat_model: str = "gpt-4o-mini"

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- ingest / analytics ---
    # Accept a step but flag it if the energy balance residual exceeds this (kW).
    balance_epsilon_kw: float = 0.05

    @property
    def feed_in_default(self) -> float:
        """Fallback feed-in rate if a tariff is missing one (€/kWh)."""
        return 0.081


@lru_cache
def get_settings() -> Settings:
    return Settings()
