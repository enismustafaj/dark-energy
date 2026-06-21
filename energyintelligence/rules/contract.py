"""Contract-intelligence rules.

Advise switching to a tariff that fits the household's actual energy profile,
costed by re-pricing the year's real grid imports under each alternative tariff.
"""

from __future__ import annotations

import pandas as pd

from ..models import Advice, Fact, RuleResult
from .base import RuleContext, register


@register
class TariffFit:
    key = "tariff_fit"
    category = "contract"
    device_category = None

    def applies(self, ctx: RuleContext) -> bool:
        return ctx.contract is not None

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        current = ctx.contract["tariff_id"]
        others = ctx.conn.execute(
            "SELECT tariff_id,type,energy_rate_eur_per_kwh,spot_adder_eur_per_kwh,"
            "base_fee_eur_per_month FROM tariffs WHERE tariff_id != ?", (current,)
        ).fetchall()
        if not others:
            return None

        # Hourly spot prices for repricing dynamic alternatives.
        spot = pd.Series({
            pd.to_datetime(r["ts"]): r["spot_price_eur_per_kwh"]
            for r in ctx.conn.execute("SELECT ts,spot_price_eur_per_kwh FROM dynamic_prices")
        }).sort_index()

        baseline_annual = ctx.annualize(ctx.baseline_cost.total_eur)
        best = None
        for t in others:
            if t["type"] == "fixed_rate":
                cf = ctx.replay(flat_rate=t["energy_rate_eur_per_kwh"],
                                base_fee_eur_per_month=t["base_fee_eur_per_month"])
            else:
                retail = spot + (t["spot_adder_eur_per_kwh"] or 0.0)
                cf = ctx.replay(price_series=retail,
                                base_fee_eur_per_month=t["base_fee_eur_per_month"])
            cf_annual = ctx.annualize(cf.total_eur)
            benefit = round(baseline_annual - cf_annual, 0)
            if best is None or benefit > best[1]:
                best = (t, benefit, cf_annual)

        tariff, benefit, cf_annual = best
        if benefit <= 0:
            return None  # current tariff is already the best fit — no advice

        notice = ctx.contract["notice_period_weeks"]
        fact = Fact(
            key="tariff_fit", household_id=ctx.household_id, type="insight",
            category="contract", severity="info", period="annual",
            title=f"A different tariff fits your usage better",
            detail=(f"On the {tariff['tariff_id']} tariff your annual energy cost would be "
                    f"about €{cf_annual:.0f} versus €{baseline_annual:.0f} today — "
                    f"a saving of about €{benefit:.0f} per year. "
                    f"Switching is subject to your {notice}-week notice period."),
            numbers={"current_eur": round(baseline_annual, 0),
                     "alternative_eur": round(cf_annual, 0),
                     "benefit_eur": benefit, "notice_weeks": notice},
            template_id="tariff_fit",
            suggested_action_key="suggest_tariff_switch",
        )
        return RuleResult(fact=fact, advice=Advice(
            description=f"Switch to the {tariff['tariff_id']} tariff.",
            baseline_cost_eur=round(baseline_annual, 0),
            counterfactual_cost_eur=round(cf_annual, 0),
            benefit_eur=benefit,
            catalog_ref=tariff["tariff_id"],
            action_key="suggest_tariff_switch",
        ))
