"""Action interface + registry.

Actions are real endpoints with mocked internal effects. Each action validates
feasibility against the household's assets (so e.g. an EV-charge action 409s for
a home with no EV charger), then executes via a ``DeviceAdapter``. The clean
``validate`` / ``execute`` split is what makes adding a new action — or swapping
in a real adapter — a small, local change.
"""

from __future__ import annotations

import sqlite3
from typing import Protocol

from ..models import ActionEffect
from .adapters import DeviceAdapter


class ActionError(Exception):
    """Raised when an action is not applicable to a household (-> HTTP 409)."""


class Action(Protocol):
    type: str
    label: str

    def validate(self, conn: sqlite3.Connection, household_id: str, params: dict) -> None:
        ...

    def execute(
        self, conn: sqlite3.Connection, household_id: str, params: dict,
        adapter: DeviceAdapter,
    ) -> ActionEffect:
        ...


_REGISTRY: dict[str, Action] = {}


def register(action_cls: type[Action]) -> type[Action]:
    """Class decorator — instantiates the action and stores it in the registry."""
    instance = action_cls()
    _REGISTRY[instance.type] = instance
    return action_cls


def get_action(action_type: str) -> Action | None:
    return _REGISTRY.get(action_type)


def all_actions() -> list[Action]:
    return list(_REGISTRY.values())


# --- shared helpers --------------------------------------------------------

def _household(conn: sqlite3.Connection, household_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM households WHERE household_id=?", (household_id,)
    ).fetchone()
    if row is None:
        raise ActionError(f"Unknown household {household_id}")
    return row
