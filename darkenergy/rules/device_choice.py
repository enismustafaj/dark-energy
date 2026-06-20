"""Device-choice intelligence rules.

Advise buying, replacing, or upsizing an appliance, costed against the
household's real telemetry via counterfactual replay and ranked by annual
benefit. Each rule consults the qualified-appliance catalog.

  * heatpump_upgrade — a higher-SCOP heat pump draws less electricity for the
    same heat; replay the heat-pump load scaled by the SCOP ratio.
  * add_battery — a PV home with no battery; model a catalog battery storing
    surplus that is currently exported and discharging it against later import.
  * battery_upsize — a home that still exports surplus while importing later;
    model a larger battery capturing more of that surplus.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..analytics.costing import scale_device_load
from ..models import Advice, Fact, RuleResult
from .base import RuleContext, register

STEP_HOURS = 0.25


def _payback(capex: float | None, benefit: float) -> float | None:
    if not capex or benefit <= 0:
        return None
    return round(capex / benefit, 1)


def _battery_dispatch_transform(capacity_kwh: float, power_kw: float, efficiency: float,
                                neutralize_existing: bool = False):
    """Simulate a battery that charges from surplus (export) and discharges against
    grid import, per 15-min step, resetting SoC each day.

    When ``neutralize_existing`` is set, the household's *recorded* battery flows
    are first reversed back into the grid position (export += recorded discharge,
    import += recorded charge) so the simulated battery fully *replaces* the one
    already in the telemetry. This is required for upsize/activation advice on a
    home that already has a battery — otherwise the recorded battery's effect is
    double-counted. For ``add_battery`` (no battery in the data) leave it False.

    Returns a transform that reduces import (and the export it consumed), so
    ``replay_cost`` then prices the improved grid position.
    """
    def _t(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        imp = out["grid_import_kw"].fillna(0).to_numpy(copy=True)
        exp = out["grid_export_kw"].fillna(0).to_numpy(copy=True)
        if neutralize_existing:
            # Undo the recorded battery: its discharge had reduced import / fed the
            # home (add it back as if grid-supplied); its charge had drawn power
            # (give it back as available surplus).
            rec_charge = out["battery_charge_kw"].fillna(0).to_numpy()
            rec_discharge = out["battery_discharge_kw"].fillna(0).to_numpy()
            # Net effect of the recorded battery on the grid, reversed:
            net = imp - exp + rec_discharge - rec_charge
            imp = net.clip(min=0)
            exp = (-net).clip(min=0)
        soc = 0.0
        cur_day = None
        idx = out.index
        for i in range(len(out)):
            day = idx[i].date()
            if day != cur_day:
                cur_day, soc = day, 0.0
            if exp[i] > 0 and soc < capacity_kwh:
                charge_kw = min(exp[i], power_kw, (capacity_kwh - soc) / STEP_HOURS)
                soc += charge_kw * STEP_HOURS * efficiency
                exp[i] -= charge_kw
            if imp[i] > 0 and soc > 0:
                dis_kw = min(imp[i], power_kw, soc / STEP_HOURS)
                soc -= dis_kw * STEP_HOURS
                imp[i] -= dis_kw
        out["grid_import_kw"] = imp
        out["grid_export_kw"] = exp
        return out
    return _t


@register
class HeatpumpUpgrade:
    key = "heatpump_upgrade"
    category = "device_choice"
    device_category = "heat_pump"

    def applies(self, ctx: RuleContext) -> bool:
        return ctx.has("heat_pump") and bool(ctx.catalog_for("heat_pump"))

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        dev = ctx.device("heat_pump")
        current_scop = dev["efficiency"] or 3.2
        rated = dev["rated_kw"] or 0.0
        candidates = [c for c in ctx.catalog_for("heat_pump")
                      if (c["rated_kw"] or 0) >= rated * 0.9
                      and (c["efficiency"] or 0) > current_scop + 0.1]
        if not candidates:
            return None

        # Evaluate every meaningfully-better unit and pick the one with the
        # shortest payback (best value), not merely the highest SCOP — a marginally
        # better, far pricier unit shouldn't win.
        best = None  # (candidate, benefit, payback, cf)
        for c in candidates:
            factor = current_scop / c["efficiency"]
            cf = ctx.replay(transform=scale_device_load("heatpump_kw", factor))
            benefit = round(ctx.annualize(ctx.baseline_cost.total_eur - cf.total_eur), 0)
            if benefit <= 0:
                continue
            payback = _payback(c["capex_eur"], benefit)
            if best is None or (payback or 1e9) < (best[2] or 1e9):
                best = (c, benefit, payback, cf)
        if best is None:
            return None
        best, benefit, payback, cf = best
        payback_disp = round(payback) if payback else None

        fact = Fact(
            key="heatpump_upgrade", household_id=ctx.household_id, type="insight",
            category="device_choice", device_id=dev["id"], severity="info",
            period="annual", title="A more efficient heat pump would cut your bill",
            detail=(f"Your heat pump runs at about SCOP {current_scop:.1f}. The "
                    f"{best['make_model']} (SCOP {best['efficiency']:.1f}) would use less "
                    f"electricity for the same heat — about €{benefit:.0f} less per year"
                    + (f", paying back its €{best['capex_eur']:.0f} cost in roughly "
                       f"{payback_disp} years." if payback_disp else ".")),
            numbers={"make_model": best["make_model"],
                     "current_scop": round(current_scop, 1),
                     "new_scop": round(best["efficiency"], 1),
                     "benefit_eur": benefit, "capex_eur": round(best["capex_eur"], 0),
                     **({"payback_years": payback_disp} if payback_disp else {})},
            template_id="heatpump_upgrade", suggested_action_key=None,
        )
        return RuleResult(fact=fact, advice=Advice(
            description=f"Replace with {best['make_model']} (SCOP {best['efficiency']:.1f}).",
            baseline_cost_eur=round(ctx.annualize(ctx.baseline_cost.total_eur), 0),
            counterfactual_cost_eur=round(ctx.annualize(cf.total_eur), 0),
            benefit_eur=benefit, capex_eur=round(best["capex_eur"], 0),
            payback_years=payback, catalog_ref=best["id"],
        ))


@register
class AddBattery:
    key = "add_battery"
    category = "device_choice"
    device_category = "battery"

    def applies(self, ctx: RuleContext) -> bool:
        # PV home with no battery and meaningful export to capture.
        return (ctx.has("pv") and not ctx.has("battery")
                and bool(ctx.catalog_for("battery"))
                and ctx.status.grid_export_kwh > 200)

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        # Pick the battery whose net annual benefit is highest.
        best = None
        for cand in ctx.catalog_for("battery"):
            transform = _battery_dispatch_transform(
                cand["capacity_kwh"], cand["power_kw"], cand["efficiency"] or 0.9)
            cf = ctx.replay(transform=transform)
            benefit = round(ctx.annualize(ctx.baseline_cost.total_eur - cf.total_eur), 0)
            if best is None or benefit > best[1]:
                best = (cand, benefit, cf)
        cand, benefit, cf = best
        if benefit <= 0:
            return None
        payback = _payback(cand["capex_eur"], benefit)
        payback_disp = round(payback) if payback else None
        fact = Fact(
            key="add_battery", household_id=ctx.household_id, type="insight",
            category="device_choice", severity="info", period="annual",
            title="A home battery would capture your unused solar",
            detail=(f"You export a lot of solar that you later buy back from the grid. "
                    f"A {cand['make_model']} ({cand['capacity_kwh']:.0f} kWh) battery would "
                    f"store that surplus and save about €{benefit:.0f} per year"
                    + (f", paying back its €{cand['capex_eur']:.0f} cost in roughly "
                       f"{payback_disp} years." if payback_disp else ".")),
            numbers={"make_model": cand["make_model"],
                     "capacity_kwh": round(cand["capacity_kwh"], 0),
                     "benefit_eur": benefit, "capex_eur": round(cand["capex_eur"], 0),
                     **({"payback_years": payback_disp} if payback_disp else {})},
            template_id="add_battery", suggested_action_key=None,
        )
        return RuleResult(fact=fact, advice=Advice(
            description=f"Install {cand['make_model']} ({cand['capacity_kwh']:.0f} kWh).",
            baseline_cost_eur=round(ctx.annualize(ctx.baseline_cost.total_eur), 0),
            counterfactual_cost_eur=round(ctx.annualize(cf.total_eur), 0),
            benefit_eur=benefit, capex_eur=round(cand["capex_eur"], 0),
            payback_years=payback, catalog_ref=cand["id"],
        ))


@register
class BatteryUpsize:
    key = "battery_upsize"
    category = "device_choice"
    device_category = "battery"

    def applies(self, ctx: RuleContext) -> bool:
        # Has a battery, still exports meaningfully (surplus the battery misses),
        # and a larger catalog battery exists.
        if not (ctx.has("battery") and bool(ctx.catalog_for("battery"))):
            return False
        return ctx.status.grid_export_kwh > 300

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        dev = ctx.device("battery")
        cur_cap = dev["capacity_kwh"] or 0.0
        bigger = [c for c in ctx.catalog_for("battery")
                  if (c["capacity_kwh"] or 0) > cur_cap + 1.0]
        if not bigger:
            return None
        # Apples-to-apples: both the current-size and candidate batteries replace
        # the recorded battery (neutralize_existing) so the marginal benefit is the
        # extra capture from going bigger, not an artifact of double-counting.
        cur_t = _battery_dispatch_transform(cur_cap, dev["power_kw"] or 5.0,
                                            dev["efficiency"] or 0.9,
                                            neutralize_existing=True)
        cur_cf = ctx.replay(transform=cur_t)
        best = None
        for cand in bigger:
            t = _battery_dispatch_transform(cand["capacity_kwh"], cand["power_kw"],
                                            cand["efficiency"] or 0.9,
                                            neutralize_existing=True)
            cf = ctx.replay(transform=t)
            benefit = round(ctx.annualize(cur_cf.total_eur - cf.total_eur), 0)
            if best is None or benefit > best[1]:
                best = (cand, benefit, cf)
        cand, benefit, cf = best
        if benefit <= 0:
            return None
        payback = _payback(cand["capex_eur"], benefit)
        fact = Fact(
            key="battery_upsize", household_id=ctx.household_id, type="insight",
            category="device_choice", device_id=dev["id"], severity="info",
            period="annual", title="A larger battery would store more of your solar",
            detail=(f"Your {cur_cap:.0f} kWh battery still lets surplus solar spill to the "
                    f"grid. Upgrading to the {cand['make_model']} ({cand['capacity_kwh']:.0f} "
                    f"kWh) would capture more of it — about €{benefit:.0f} more per year."),
            numbers={"make_model": cand["make_model"], "current_kwh": round(cur_cap, 0),
                     "new_kwh": round(cand["capacity_kwh"], 0),
                     "benefit_eur": benefit, "capex_eur": round(cand["capex_eur"], 0),
                     **({"payback_years": payback} if payback else {})},
            template_id="battery_upsize", suggested_action_key=None,
        )
        return RuleResult(fact=fact, advice=Advice(
            description=f"Upgrade to {cand['make_model']} ({cand['capacity_kwh']:.0f} kWh).",
            baseline_cost_eur=round(ctx.annualize(cur_cf.total_eur), 0),
            counterfactual_cost_eur=round(ctx.annualize(cf.total_eur), 0),
            benefit_eur=benefit, capex_eur=round(cand["capex_eur"], 0),
            payback_years=payback, catalog_ref=cand["id"],
        ))
