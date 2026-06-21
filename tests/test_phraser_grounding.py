"""The phrasing layer must never surface a number that isn't in the Fact."""

from __future__ import annotations

from energyintelligence.ai.prompts import allowed_numbers, grounding_violations
from energyintelligence.ai.template_phraser import TemplatePhraser
from energyintelligence.models import Fact, FactBundle


def test_grounding_check_flags_hallucinated_number():
    nums = {"excess_pct": 24, "days": 10}
    assert grounding_violations("ran 24% over for 10 days", nums) == set()
    assert grounding_violations("ran 24% over for 10 days, costing 99 EUR", nums) == {"99"}


def test_allowed_numbers_includes_date_parts():
    allowed = allowed_numbers({"high_month": "2025-08", "high_eur": 221.65})
    assert "2025" in allowed and "8" in allowed and "221.65" in allowed


def test_template_phraser_only_uses_fact_numbers():
    fact = Fact(
        key="heatpump_overconsumption", household_id="HH-1001", type="anomaly",
        severity="high", period="x", numbers={"excess_pct": 37, "days": 10},
        template_id="heatpump_overconsumption",
        suggested_action_key="shift_heatpump_to_cheap_window",
    )
    bundle = FactBundle(household_id="HH-1001", facts=[fact], context={"name": "Test"})
    out = TemplatePhraser().phrase(bundle)
    assert len(out) == 1
    text = f"{out[0].title} {out[0].body}"
    assert grounding_violations(text, fact.numbers) == set()
    assert out[0].action_label == "Shift heat pump to cheap hours"
