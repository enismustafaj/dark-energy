"""Deterministic, no-API-key phraser. The default backend.

Each ``template_id`` maps to a format string whose slots are filled only from
``Fact.numbers`` plus string-only ``context`` — so it is structurally impossible
for it to surface an ungrounded number. This also serves as the fallback when an
LLM backend is unavailable or fails its grounding check.
"""

from __future__ import annotations

from ..models import FactBundle, PhrasedInsight

# action_key -> button label shown on the insight card
ACTION_LABELS = {
    "schedule_ev_charge": "Schedule EV charging",
    "shift_heatpump_to_cheap_window": "Shift heat pump to cheap hours",
    "set_battery_reserve": "Set battery reserve",
    "suggest_tariff_switch": "Compare tariffs",
}

TEMPLATES = {
    "heatpump_overconsumption": {
        "title": "Heat pump using more than usual",
        "body": ("Your heat pump ran about {excess_pct:.0f}% above its normal level for "
                 "{days:.0f} days — beyond what the weather explains. This often points to a "
                 "defrost fault, low refrigerant, or a thermostat setting. Worth a check."),
    },
    "high_baseload": {
        "title": "High always-on standby",
        "body": ("Your overnight standby load is around {baseload_kw} kW — about "
                 "{ratio_pct:.0f}% of your average draw, or roughly {annual_kwh:.0f} kWh a year. "
                 "Tracking down always-on devices could cut this."),
    },
    "bill_spike": {
        "title": "Your highest bill was in {high_month}",
        "body": ("{high_month} came to €{high_eur:.2f}, versus your lowest month "
                 "({low_month}) at €{low_eur:.2f}. Pre-heating during cheap or sunny hours "
                 "helps flatten months like this."),
    },
    "cheapest_window": {
        "title": "Cheapest power around {cheap_hour:02d}:00",
        "body": ("The cheapest electricity recently has been around {cheap_hour:02d}:00 at "
                 "about €{cheap_price_eur:.3f}/kWh. Shifting flexible loads — EV, dishwasher, "
                 "laundry — into that window saves money."),
    },
}


class TemplatePhraser:
    name = "template"

    def phrase(self, bundle: FactBundle) -> list[PhrasedInsight]:
        out: list[PhrasedInsight] = []
        for fact in bundle.facts:
            tpl = TEMPLATES.get(fact.template_id)
            slots = {**fact.numbers, **bundle.context}
            if tpl is None:
                # Unknown template → fall back to the fact's own pre-written text.
                title, body = fact.title, fact.detail
            else:
                try:
                    title = tpl["title"].format(**slots)
                    body = tpl["body"].format(**slots)
                except (KeyError, ValueError, IndexError):
                    title, body = fact.title, fact.detail
            out.append(PhrasedInsight(
                fact_key=fact.key,
                title=title,
                body=body,
                action_label=ACTION_LABELS.get(fact.suggested_action_key or ""),
            ))
        return out
