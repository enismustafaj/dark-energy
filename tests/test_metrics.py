"""Metrics must reproduce the ground-truth monthly bills."""

from __future__ import annotations

import pytest

from energyintelligence.analytics import frames, metrics
from energyintelligence.db import get_contract


@pytest.mark.parametrize("hh", ["HH-1001", "HH-1002", "HH-1003", "HH-1004"])
def test_january_totals_match_ground_truth(conn, hh):
    gt = conn.execute(
        "SELECT * FROM monthly_bills WHERE household_id=? AND month='2025-01'", (hh,)
    ).fetchone()
    contract = get_contract(conn, hh)
    df = frames.load_window(conn, hh, "2025-01-01T00:00:00", "2025-02-01T00:00:00")
    tot = metrics.energy_totals(df)
    cost = metrics.energy_cost(
        df, contract["feed_in_eur_per_kwh"], contract["base_fee_eur_per_month"],
        days_in_period=31,
    )
    assert tot.consumption_kwh == pytest.approx(gt["consumption_kwh"], abs=0.5)
    assert tot.pv_production_kwh == pytest.approx(gt["pv_production_kwh"], abs=0.5)
    assert tot.self_sufficiency_pct == pytest.approx(gt["self_sufficiency_pct"], abs=0.5)
    assert cost["energy_cost_eur"] == pytest.approx(gt["energy_cost_eur"], abs=0.5)
    assert cost["total_eur"] == pytest.approx(gt["total_bill_eur"], abs=0.5)


def test_hh1004_degrades_gracefully(conn):
    """No battery/HP/EV → breakdown returns those shares as 0, no crash."""
    df = frames.load_window(conn, "HH-1004", "2025-01-01", "2025-02-01")
    bd = metrics.device_breakdown(df)
    assert bd["heatpump_pct"] == 0.0
    assert bd["ev_pct"] == 0.0
    assert metrics.energy_totals(df).consumption_kwh > 0
