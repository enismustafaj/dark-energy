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


STEP_HOURS = 0.25

# Conservative share of always-on standby draw a household can realistically
# trim by hunting down phantom loads (idle electronics, chargers, old fridges).
STANDBY_REDUCIBLE_FRAC = 0.30

# Conservative share of annual consumption that is actually time-shiftable
# (EV charging, dishwasher, laundry, water heating) and could move into the
# cheapest window. Kept low so the saving is defensible, not aspirational.
SHIFTABLE_FRAC = 0.15


def _avg_import_price(ctx: RuleContext) -> float:
    """The home's own effective €/kWh on imported energy, straight from its
    telemetry: total energy cost ÷ total imported kWh. This is the honest basis
    for any 'reduce/shift consumption' saving — it's the price they actually pay.
    """
    df = ctx.df
    import_kwh = float((df["grid_import_kw"].fillna(0) * STEP_HOURS).sum())
    if import_kwh <= 0:
        return 0.0
    # baseline_cost.energy_cost_eur is the priced grid import over the same window.
    return ctx.baseline_cost.energy_cost_eur / import_kwh


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
    category = "utilization"
    device_category = None   # whole-home; not tied to a clickable device node

    def applies(self, ctx: RuleContext) -> bool:
        return True

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        facts = anomalies.detect_high_baseload(ctx.conn, ctx.household_id)
        if not facts:
            return None
        # A high always-on standby is a usage nudge, not an equipment fault — the
        # detector marks it type="nudge". Categorise it accordingly.
        fact = facts[0]
        fact.category = "utilization"

        # Grounded saving: trimming a conservative share of the always-on draw,
        # valued at the price this home actually pays for imported energy. Cap the
        # standby energy at the home's actual annual grid import — solar/battery
        # already cover the rest, so cutting it yields no cash saving.
        annual_kwh = float(fact.numbers.get("annual_kwh", 0.0))
        import_kwh = float((ctx.df["grid_import_kw"].fillna(0) * STEP_HOURS).sum())
        annual_import_kwh = ctx.annualize(import_kwh)
        reducible_kwh = min(annual_kwh, annual_import_kwh)
        price = _avg_import_price(ctx)
        benefit = round(reducible_kwh * STANDBY_REDUCIBLE_FRAC * price, 0)
        if benefit <= 0:
            return RuleResult(fact=fact)
        fact.numbers["benefit_eur"] = benefit
        return RuleResult(fact=fact, advice=Advice(
            description="Trim always-on standby (idle electronics, chargers, old appliances).",
            benefit_eur=benefit,
        ))


@register
class BillSpike:
    key = "bill_spike"
    category = "utilization"
    device_category = None

    def applies(self, ctx: RuleContext) -> bool:
        return True

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        facts = anomalies.detect_bill_spike(ctx.conn, ctx.household_id)
        if not facts:
            return None
        # A standout monthly bill is an informational insight (usually seasonal),
        # not a fault. Keep it out of the fault bucket.
        fact = facts[0]
        fact.category = "utilization"
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

        # Grounded saving: move a conservative slice of flexible load from the
        # price this home pays on average into the cheapest window, valued at the
        # per-kWh gap between the two.
        cheap_price = float(fact.numbers.get("cheap_price_eur", 0.0))
        price = _avg_import_price(ctx)
        gap = price - cheap_price
        consumption_kwh = float(ctx.status.consumption_kwh or 0.0)  # annualized
        benefit = round(consumption_kwh * SHIFTABLE_FRAC * gap, 0) if gap > 0 else 0.0
        if benefit <= 0:
            return RuleResult(fact=fact)
        fact.numbers["benefit_eur"] = benefit
        return RuleResult(fact=fact, advice=Advice(
            description="Shift flexible loads (EV, dishwasher, laundry) into the cheapest window.",
            benefit_eur=benefit,
            action_key="schedule_ev_charge",
        ))
