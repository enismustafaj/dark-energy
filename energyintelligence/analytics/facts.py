"""Helpers for building the ``Fact`` / ``FactBundle`` contract.

A Fact carries the *computed numbers* and a ``template_id``; the AI phrasing
layer turns it into user-facing text but may never introduce a number that is
not in ``Fact.numbers``. Keeping construction here makes the grounding contract
explicit and reusable across detectors and forecasts.
"""

from __future__ import annotations

import sqlite3

from ..models import Fact, FactBundle


def build_bundle(conn: sqlite3.Connection, household_id: str, facts: list[Fact]) -> FactBundle:
    """Wrap facts with household context (strings only — no numbers leak in as
    free text the phraser might mistake for groundable figures)."""
    h = conn.execute(
        "SELECT name, city, tariff_id FROM households WHERE household_id = ?",
        (household_id,),
    ).fetchone()
    context = {}
    if h is not None:
        context = {"name": h["name"] or "", "city": h["city"] or "",
                   "tariff": h["tariff_id"] or ""}
    return FactBundle(household_id=household_id, facts=facts, context=context)


def fact_to_event_row(fact: Fact, phrased_text: str | None = None) -> dict:
    """Map a Fact to the columns of an ``insight_events`` upsert (origin=detected)."""
    return {
        "household_id": fact.household_id,
        "type": fact.type,
        "severity": fact.severity,
        "period": fact.period,
        "title": fact.title,
        "detail": fact.detail,
        "suggested_action": fact.suggested_action_key,
        "fact_key": fact.key,
        "fact_json": fact.model_dump_json(),
        "phrased_text": phrased_text,
        "origin": "detected",
    }
