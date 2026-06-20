"""Generate two *showcase* households that surface the live ruleset's insights.

The original four homes (HH-1001..1004) were used to build the system and their
dashboard advice was seeded from ``insight_events.json``. These two new homes
are engineered so that the rules in ``hauswatt.rules`` fire on their own — no
seeded insights involved:

  HH-2001 (derived from HH-1004 — PV, no battery):
    * add_battery            — PV home, no battery, large export to capture
    * high_baseload          — overnight standby raised above 60% of avg load
    * bill_spike             — one outlier month in monthly_bills (>=1.5x median)

  HH-2002 (derived from HH-1002 — heat pump + small battery):
    * heatpump_overconsumption — a ~10-day winter stretch inflated >15% above the
                                 temperature-normalised norm (sustained fault)
    * battery_upsize           — small battery still spilling export to the grid
    * heatpump_upgrade         — heat pump vs higher-SCOP catalog unit (natural)

Every mutated step is rebalanced so the dataset invariant still holds exactly:
    pv + grid_import + battery_discharge = total_consumption + grid_export + battery_charge

Run from the repo root:  python scripts/generate_showcase_households.py
It rewrites the timeseries/bills for the new homes and appends their entries to
households.json / contracts.json (idempotent — re-running replaces them).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "enpal-track-dataset"

# House-load floor (kW) applied to the 01:00–05:00 band of HH-2001 so its
# overnight median clears 60% of the all-hours average (high_baseload trigger).
NIGHT_BASELOAD_KW = 0.42

# Winter overconsumption window for HH-2002 (inclusive date range) and the factor
# the heat-pump draw is multiplied by inside it. 40% over norm comfortably clears
# the rule's +15% threshold across the required minimum run length.
HP_FAULT_START = "2025-01-13"
HP_FAULT_END = "2025-01-24"
HP_FAULT_FACTOR = 1.4


def _load(name: str):
    return json.loads((DATASET / name).read_text())


def _rebalance(rec: dict) -> None:
    """Recompute total_consumption and the grid position after a load change.

    Keeps battery flows as recorded; solves the net grid position so the balance
    invariant holds, then splits it into import (>0) / export (<0).
    """
    rec["total_consumption_kw"] = round(
        rec["house_load_kw"] + rec.get("heatpump_kw", 0.0) + rec.get("ev_charging_kw", 0.0),
        3,
    )
    # pv + import + discharge = consumption + export + charge
    #   net_grid = import - export = consumption + charge - pv - discharge
    net = (
        rec["total_consumption_kw"]
        + rec.get("battery_charge_kw", 0.0)
        - rec.get("pv_production_kw", 0.0)
        - rec.get("battery_discharge_kw", 0.0)
    )
    rec["grid_import_kw"] = round(max(net, 0.0), 3)
    rec["grid_export_kw"] = round(max(-net, 0.0), 3)


def _hour(rec: dict) -> int:
    return int(rec["timestamp"][11:13])


# --- HH-2001: add_battery + high_baseload + bill_spike ----------------------

def build_hh2001() -> None:
    src = _load("energy_timeseries_HH-1004.json")
    recs = copy.deepcopy(src["records"])

    for r in recs:
        # Raise overnight standby to create a high always-on baseload signature.
        if 1 <= _hour(r) < 5:
            r["house_load_kw"] = round(max(r["house_load_kw"], NIGHT_BASELOAD_KW), 3)
            _rebalance(r)
        # Leave the rest as-is: HH-1004 already has PV, no battery, and ~3.8 MWh
        # of export — exactly what add_battery looks for.

    out = {
        "household_id": "HH-2001",
        "resolution_minutes": 15,
        "year": src.get("year", 2025),
        "records": recs,
    }
    (DATASET / "energy_timeseries_HH-2001.json").write_text(json.dumps(out))
    print(f"HH-2001: wrote {len(recs)} records")


# --- HH-2002: heatpump_overconsumption + battery_upsize + heatpump_upgrade ---

def build_hh2002() -> None:
    src = _load("energy_timeseries_HH-1002.json")
    recs = copy.deepcopy(src["records"])

    inflated = 0
    for r in recs:
        day = r["timestamp"][:10]
        if HP_FAULT_START <= day <= HP_FAULT_END and r.get("heatpump_kw", 0.0) > 0:
            r["heatpump_kw"] = round(r["heatpump_kw"] * HP_FAULT_FACTOR, 3)
            _rebalance(r)
            inflated += 1
        # Battery flows are kept; HH-1002 already exports ~1.9 MWh/yr, which the
        # downsized 5 kWh battery (see contract below) leaves largely uncaptured,
        # so battery_upsize sees plenty of surplus to go after.

    out = {
        "household_id": "HH-2002",
        "resolution_minutes": 15,
        "year": src.get("year", 2025),
        "records": recs,
    }
    (DATASET / "energy_timeseries_HH-2002.json").write_text(json.dumps(out))
    print(f"HH-2002: wrote {len(recs)} records ({inflated} heat-pump steps inflated)")


# --- monthly bills ----------------------------------------------------------

def _bills_from_series(household_id: str, ts_file: str, base_fee: float,
                       feed_in: float, spike_month: str | None,
                       spike_factor: float) -> list[dict]:
    """Roll a timeseries up to monthly bills using its own per-step price, so the
    figures match the telemetry. Optionally amplify one month into a spike."""
    recs = _load(ts_file)["records"]
    months: dict[str, dict] = {}
    for r in recs:
        m = r["timestamp"][:7]
        b = months.setdefault(m, dict(cons=0.0, pv=0.0, imp=0.0, exp=0.0, energy=0.0))
        b["cons"] += r["total_consumption_kw"] * 0.25
        b["pv"] += r["pv_production_kw"] * 0.25
        b["imp"] += r["grid_import_kw"] * 0.25
        b["exp"] += r["grid_export_kw"] * 0.25
        b["energy"] += r["grid_import_kw"] * 0.25 * (r.get("price_eur_per_kwh") or 0.0)

    out = []
    for m in sorted(months):
        b = months[m]
        energy = b["energy"]
        if spike_month and m == spike_month:
            energy *= spike_factor  # one stand-out high-cost month
        feed_credit = b["exp"] * feed_in
        total = energy + base_fee - feed_credit
        ss = (1 - b["imp"] / b["cons"]) * 100 if b["cons"] else 0.0
        out.append({
            "household_id": household_id,
            "month": m,
            "consumption_kwh": round(b["cons"], 1),
            "pv_production_kwh": round(b["pv"], 1),
            "grid_import_kwh": round(b["imp"], 1),
            "grid_export_kwh": round(b["exp"], 1),
            "energy_cost_eur": round(energy, 2),
            "base_fee_eur": base_fee,
            "feed_in_credit_eur": round(feed_credit, 2),
            "total_bill_eur": round(total, 2),
            "self_sufficiency_pct": round(ss, 1),
        })
    return out


def update_monthly_bills() -> None:
    bills = [b for b in _load("monthly_bills.json")
             if b["household_id"] not in ("HH-2001", "HH-2002")]
    # HH-2001: dynamic tariff (base fee 12.9), one bill spike in January.
    bills += _bills_from_series("HH-2001", "energy_timeseries_HH-2001.json",
                                base_fee=12.9, feed_in=0.081,
                                spike_month="2025-01", spike_factor=1.9)
    # HH-2002: dynamic tariff, no engineered spike.
    bills += _bills_from_series("HH-2002", "energy_timeseries_HH-2002.json",
                                base_fee=12.9, feed_in=0.081,
                                spike_month=None, spike_factor=1.0)
    (DATASET / "monthly_bills.json").write_text(json.dumps(bills, indent=2))
    print(f"monthly_bills: {len(bills)} rows total")


# --- household + contract metadata ------------------------------------------

NEW_HOUSEHOLDS = [
    {
        "household_id": "HH-2001",
        "name": "Familie Wagner",
        "city": "Stuttgart",
        "residents": 4,
        "pv_kwp": 8.5,
        "battery_kwh": 0.0,
        "battery_power_kw": 0.0,
        "heat_pump": False,
        "ev_charger": False,
        "tariff_id": "dynamic",
        "timeseries_file": "energy_timeseries_HH-2001.json",
    },
    {
        "household_id": "HH-2002",
        "name": "Familie Koch",
        "city": "Dresden",
        "residents": 3,
        "pv_kwp": 6.4,
        "battery_kwh": 5.0,
        "battery_power_kw": 2.5,
        "heat_pump": True,
        "ev_charger": False,
        "tariff_id": "dynamic",
        "timeseries_file": "energy_timeseries_HH-2002.json",
    },
]


def _contract_for(h: dict, hp_kw: float) -> dict:
    return {
        "household_id": h["household_id"],
        "customer_name": h["name"],
        "supply_address": {"city": h["city"], "country": "DE"},
        "provider": "Enpal",
        "tariff_id": "dynamic",
        "tariff_name": "Enpal FlexStrom Dynamic",
        "contract_start": "2024-06-01",
        "contract_end": "2026-06-01",
        "minimum_term_months": 24,
        "notice_period_weeks": 6,
        "auto_renew_months": 12,
        "base_fee_eur_per_month": 12.9,
        "energy_pricing": {"model": "dynamic_hourly", "spot_adder_eur_per_kwh": 0.119},
        "feed_in_eur_per_kwh": 0.081,
        "assets": {
            "pv_kwp": h["pv_kwp"],
            "battery_kwh": h["battery_kwh"],
            "battery_power_kw": h["battery_power_kw"],
            "heat_pump": h["heat_pump"],
            "heat_pump_kw": hp_kw,
            "ev_charger": h["ev_charger"],
            "ev_battery_kwh": 0,
        },
        "contract_terms_text": (
            f"This agreement between Enpal B.V. and {h['name']} commences on "
            f"2024-06-01 for a minimum term of 24 months ending 2026-06-01. The "
            f"dynamic hourly electricity tariff applies. A monthly base fee of EUR "
            f"12.90 is charged. Surplus PV energy exported to the grid is "
            f"remunerated at EUR 0.081 per kWh. Notice of termination must be given "
            f"at least 6 weeks before the end of the term, otherwise the contract "
            f"renews automatically for 12 months."
        ),
    }


def update_metadata() -> None:
    households = [h for h in _load("households.json")
                  if h["household_id"] not in ("HH-2001", "HH-2002")]
    households += NEW_HOUSEHOLDS
    (DATASET / "households.json").write_text(json.dumps(households, indent=2))

    contracts = [c for c in _load("contracts.json")
                 if c["household_id"] not in ("HH-2001", "HH-2002")]
    contracts.append(_contract_for(NEW_HOUSEHOLDS[0], hp_kw=0.0))
    contracts.append(_contract_for(NEW_HOUSEHOLDS[1], hp_kw=7.0))
    (DATASET / "contracts.json").write_text(json.dumps(contracts, indent=2))
    print(f"households: {len(households)} | contracts: {len(contracts)}")


def main() -> None:
    build_hh2001()
    build_hh2002()
    update_monthly_bills()
    update_metadata()
    print("Done. Re-run `hauswatt seed` to load the showcase households.")


if __name__ == "__main__":
    main()
