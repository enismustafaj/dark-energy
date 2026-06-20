"""Device-utilization intelligence rules.

Suggest better use of devices that can store and dispense energy — integrating
them into the household grid (activating a battery, or using an EV for vehicle-
to-home) to cover imports that currently come from the grid at price.

Benefit is costed by modeling the device dispatching its available energy
against the household's real grid imports.
"""

from __future__ import annotations

import pandas as pd

from ..models import Advice, Fact, RuleResult
from .base import RuleContext, register
from .device_choice import _battery_dispatch_transform, _payback

STEP_HOURS = 0.25


@register
class BatteryGridSupport:
    key = "battery_grid_support"
    category = "utilization"
    device_category = "battery"

    def applies(self, ctx: RuleContext) -> bool:
        if not ctx.has("battery"):
            return False
        # There must be grid import the battery could be covering.
        return ctx.status.grid_import_kwh > 200

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        dev = ctx.device("battery")
        cap = dev["capacity_kwh"] or 0.0
        power = dev["power_kw"] or 5.0
        eff = dev["efficiency"] or 0.9

        # Marginal benefit of price-aware dispatch over the battery's *recorded*
        # behavior: neutralize the recorded battery, then re-dispatch the same-size
        # battery optimally against import. If the home already dispatches well,
        # this is ~0 and the rule correctly stays silent.
        transform = _battery_dispatch_transform(cap, power, eff, neutralize_existing=True)
        cf = ctx.replay(transform=transform)
        benefit = round(ctx.annualize(ctx.baseline_cost.total_eur - cf.total_eur), 0)
        if benefit < 20:   # not worth a nudge below ~€20/yr
            return None

        fact = Fact(
            key="battery_grid_support", household_id=ctx.household_id, type="nudge",
            category="utilization", device_id=dev["id"], severity="info",
            period="annual", title="Let your battery power the home at peak times",
            detail=(f"Configuring your {cap:.0f} kWh battery to discharge into the home "
                    f"when grid prices are high (instead of sitting idle) could save about "
                    f"€{benefit:.0f} per year."),
            numbers={"capacity_kwh": round(cap, 0), "benefit_eur": benefit},
            template_id="battery_grid_support",
            suggested_action_key="set_battery_reserve",
        )
        return RuleResult(fact=fact, advice=Advice(
            description="Enable price-aware battery discharge into the household.",
            baseline_cost_eur=round(ctx.annualize(ctx.baseline_cost.total_eur), 0),
            counterfactual_cost_eur=round(ctx.annualize(cf.total_eur), 0),
            benefit_eur=benefit, action_key="set_battery_reserve",
        ))


@register
class EvVehicleToHome:
    key = "ev_v2h"
    category = "utilization"
    device_category = "ev"

    def applies(self, ctx: RuleContext) -> bool:
        # EV present, and the home imports in the evening when an EV is usually
        # parked and could discharge. Requires a V2H-capable charger in the catalog.
        if not ctx.has("ev"):
            return False
        if ctx.status.grid_import_kwh < 200:
            return False
        return any((c["specs_json"] or "").find("v2h") >= 0
                   for c in ctx.catalog_for("ev_charger"))

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        ev = ctx.device("ev")
        ev_kwh = ev["capacity_kwh"] or 60.0
        # Model the EV as a battery available to discharge against evening import.
        # Conservative: usable share of the pack for V2H (don't drain the car).
        usable = min(ev_kwh * 0.3, 20.0)
        power = 7.0
        transform = _evening_v2h_transform(usable, power)
        cf = ctx.replay(transform=transform)
        benefit = round(ctx.annualize(ctx.baseline_cost.total_eur - cf.total_eur), 0)
        if benefit < 20:
            return None
        charger = next((c for c in ctx.catalog_for("ev_charger")
                        if "v2h" in (c["specs_json"] or "")), None)
        payback = _payback(charger["capex_eur"] if charger else None, benefit)
        fact = Fact(
            key="ev_v2h", household_id=ctx.household_id, type="nudge",
            category="utilization", device_id=ev["id"], severity="info",
            period="annual", title="Use your EV to power the home in the evening",
            detail=(f"With a vehicle-to-home charger, your EV could cover part of your "
                    f"evening grid import from its battery — about €{benefit:.0f} per year"
                    + (f", against a €{charger['capex_eur']:.0f} charger." if charger else ".")),
            numbers={"usable_kwh": round(usable, 0), "benefit_eur": benefit,
                     **({"make_model": charger["make_model"],
                         "capex_eur": round(charger["capex_eur"], 0)} if charger else {}),
                     **({"payback_years": round(payback)} if payback else {})},
            template_id="ev_v2h", suggested_action_key=None,
        )
        return RuleResult(fact=fact, advice=Advice(
            description=(f"Add a V2H charger ({charger['make_model']})." if charger
                         else "Add a vehicle-to-home charger."),
            baseline_cost_eur=round(ctx.annualize(ctx.baseline_cost.total_eur), 0),
            counterfactual_cost_eur=round(ctx.annualize(cf.total_eur), 0),
            benefit_eur=benefit, capex_eur=charger["capex_eur"] if charger else None,
            payback_years=payback, catalog_ref=charger["id"] if charger else None,
        ))


def _evening_v2h_transform(usable_kwh: float, power_kw: float):
    """Discharge a fixed daily energy budget against evening (17:00–23:00) import."""
    def _t(df: pd.DataFrame) -> pd.DataFrame:
        imp = df["grid_import_kw"].fillna(0).to_numpy(copy=True)
        idx = df.index
        cur_day = None
        budget = 0.0
        for i in range(len(df)):
            day = idx[i].date()
            if day != cur_day:
                cur_day, budget = day, usable_kwh
            hour = idx[i].hour
            if 17 <= hour < 23 and imp[i] > 0 and budget > 0:
                dis = min(imp[i], power_kw, budget / STEP_HOURS)
                imp[i] -= dis
                budget -= dis * STEP_HOURS
        out = df.copy()
        out["grid_import_kw"] = imp
        return out
    return _t
