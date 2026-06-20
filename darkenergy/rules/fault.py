"""Fault & nudge rules — the migrated anomaly detectors, now rules.

The detection logic still lives in ``analytics.anomalies`` (well-tested, pure);
these rules wrap each detector, tag the Fact with its category + the device it
concerns, and attach an advice/action where one applies (e.g. heat-pump
overconsumption → book maintenance). Faults carry no monetary benefit, so they
rank by severity within the zero-benefit tier.
"""

from __future__ import annotations

from ..analytics import anomalies
from ..models import Advice, RuleResult
from .base import RuleContext, register


def _device_id(ctx: RuleContext, category: str) -> int | None:
    d = ctx.device(category)
    return d["id"] if d is not None else None


@register
class HeatpumpOverconsumption:
    key = "heatpump_overconsumption"
    category = "fault"
    device_category = "heat_pump"

    def applies(self, ctx: RuleContext) -> bool:
        return ctx.has("heat_pump")

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        facts = anomalies.detect_heatpump_overconsumption(
            ctx.conn, ctx.household_id, has_heat_pump=True
        )
        if not facts:
            return None
        fact = facts[0]
        fact.category = "fault"
        fact.device_id = _device_id(ctx, "heat_pump")
        fact.suggested_action_key = "book_maintenance"
        return RuleResult(fact=fact, advice=Advice(
            description="Book a heat-pump service inspection to restore efficiency.",
            action_key="book_maintenance",
        ))


@register
class HighBaseload:
    key = "high_baseload"
    category = "fault"
    device_category = None   # whole-home; not tied to a clickable device node

    def applies(self, ctx: RuleContext) -> bool:
        return True

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        facts = anomalies.detect_high_baseload(ctx.conn, ctx.household_id)
        if not facts:
            return None
        fact = facts[0]
        fact.category = "fault"
        return RuleResult(fact=fact)


@register
class BillSpike:
    key = "bill_spike"
    category = "fault"
    device_category = None

    def applies(self, ctx: RuleContext) -> bool:
        return True

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        facts = anomalies.detect_bill_spike(ctx.conn, ctx.household_id)
        if not facts:
            return None
        fact = facts[0]
        fact.category = "fault"
        return RuleResult(fact=fact)


@register
class CheapestWindow:
    key = "cheapest_window"
    category = "utilization"
    device_category = None

    def applies(self, ctx: RuleContext) -> bool:
        return ctx.contract is not None

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        tariff_id = ctx.contract["tariff_id"] if ctx.contract else "dynamic"
        facts = anomalies.detect_cheapest_window(ctx.conn, ctx.household_id, tariff_id)
        if not facts:
            return None
        fact = facts[0]
        fact.category = "utilization"
        return RuleResult(fact=fact)
