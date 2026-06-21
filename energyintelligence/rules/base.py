"""Rule protocol, evaluation context, registry, and runner.

A rule is specific to a part of the device set (``device_category``), gates
itself with ``applies``, and on ``evaluate`` returns a ``RuleResult`` — a
grounded ``Fact`` plus an optional ``Advice`` whose ``benefit_eur`` comes from a
counterfactual cost replay. ``run_rules`` evaluates every applicable rule and
returns the results ranked by customer cost benefit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..analytics import status as status_mod
from ..analytics.costing import CostResult, replay_cost
from ..db import get_catalog, get_contract, get_devices
from ..models import RuleResult


@dataclass
class RuleContext:
    conn: sqlite3.Connection
    household_id: str
    status: status_mod.StatusQuo
    devices: list                  # sqlite3.Row per device
    contract: sqlite3.Row | None
    catalog: list                  # sqlite3.Row per catalog appliance
    baseline_cost: CostResult
    feed_in: float
    base_fee: float
    df: object                     # the household's full telemetry DataFrame

    # --- convenience accessors rules use ---
    def device(self, category: str):
        return next((d for d in self.devices if d["category"] == category), None)

    def has(self, category: str) -> bool:
        return self.device(category) is not None

    def catalog_for(self, category: str) -> list:
        return [c for c in self.catalog if c["category"] == category]

    def replay(self, **kw) -> CostResult:
        kw.setdefault("feed_in_eur_per_kwh", self.feed_in)
        kw.setdefault("base_fee_eur_per_month", self.base_fee)
        return replay_cost(self.df, **kw)

    def annualize(self, period_eur: float) -> float:
        return period_eur * self.status.annual_factor


@runtime_checkable
class Rule(Protocol):
    key: str
    category: str            # fault | contract | device_choice | utilization
    device_category: str | None

    def applies(self, ctx: RuleContext) -> bool: ...
    def evaluate(self, ctx: RuleContext) -> RuleResult | None: ...


_REGISTRY: list[Rule] = []


def register(rule_cls: type[Rule]) -> type[Rule]:
    _REGISTRY.append(rule_cls())
    return rule_cls


def all_rules() -> list[Rule]:
    return list(_REGISTRY)


def build_context(conn: sqlite3.Connection, household_id: str) -> RuleContext | None:
    from ..analytics import frames

    sq = status_mod.status_quo(conn, household_id)
    if sq is None:
        return None
    contract = get_contract(conn, household_id)
    feed_in = contract["feed_in_eur_per_kwh"] if contract else 0.081
    base_fee = contract["base_fee_eur_per_month"] if contract else 0.0
    df = frames.load_window(conn, household_id)
    baseline = replay_cost(df, feed_in_eur_per_kwh=feed_in, base_fee_eur_per_month=base_fee)
    return RuleContext(
        conn=conn, household_id=household_id, status=sq,
        devices=get_devices(conn, household_id), contract=contract,
        catalog=get_catalog(conn), baseline_cost=baseline,
        feed_in=feed_in, base_fee=base_fee, df=df,
    )


def run_rules(conn: sqlite3.Connection, household_id: str) -> list[RuleResult]:
    """Evaluate every applicable rule; return results ranked by benefit desc.

    Faults (no monetary benefit) sort after advice but keep their severity, so a
    high-severity fault still surfaces near the top within its benefit tier."""
    ctx = build_context(conn, household_id)
    if ctx is None:
        return []
    results: list[RuleResult] = []
    for rule in all_rules():
        try:
            if not rule.applies(ctx):
                continue
            res = rule.evaluate(ctx)
        except Exception:
            continue  # a misbehaving rule never breaks the whole engine
        if res is not None:
            results.append(res)

    sev_rank = {"high": 2, "warning": 1, "info": 0}
    results.sort(
        key=lambda r: (r.benefit_eur, sev_rank.get(r.fact.severity, 0)),
        reverse=True,
    )
    return results
