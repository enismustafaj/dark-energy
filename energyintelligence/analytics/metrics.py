"""Pure metric functions over a telemetry DataFrame (from ``frames.load_window``).

The same functions serve a full historical month and a rolling live window. All
guard against absent devices / divide-by-zero (e.g. HH-1004 has no PV-export or
battery) and return ``None`` for an undefined metric so the dashboard can hide
the corresponding card.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, asdict

import pandas as pd

from .frames import STEP_HOURS


@dataclass
class EnergyTotals:
    consumption_kwh: float
    pv_production_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    heatpump_kwh: float
    ev_kwh: float
    house_load_kwh: float
    self_sufficiency_pct: float | None
    pv_self_consumption_pct: float | None
    as_dict = asdict


def _sum_kwh(df: pd.DataFrame, kw_col: str) -> float:
    if df.empty or kw_col not in df:
        return 0.0
    return float(df[kw_col].fillna(0).sum() * STEP_HOURS)


def energy_totals(df: pd.DataFrame) -> EnergyTotals:
    consumption = _sum_kwh(df, "total_consumption_kw")
    pv = _sum_kwh(df, "pv_production_kw")
    grid_import = _sum_kwh(df, "grid_import_kw")
    grid_export = _sum_kwh(df, "grid_export_kw")

    self_suff = None
    if consumption > 0:
        self_suff = round((consumption - grid_import) / consumption * 100, 1)
    pv_self = None
    if pv > 0:
        pv_self = round((pv - grid_export) / pv * 100, 1)

    return EnergyTotals(
        consumption_kwh=round(consumption, 1),
        pv_production_kwh=round(pv, 1),
        grid_import_kwh=round(grid_import, 1),
        grid_export_kwh=round(grid_export, 1),
        heatpump_kwh=round(_sum_kwh(df, "heatpump_kw"), 1),
        ev_kwh=round(_sum_kwh(df, "ev_charging_kw"), 1),
        house_load_kwh=round(_sum_kwh(df, "house_load_kw"), 1),
        self_sufficiency_pct=self_suff,
        pv_self_consumption_pct=pv_self,
    )


def energy_cost(
    df: pd.DataFrame,
    feed_in_eur_per_kwh: float,
    base_fee_eur_per_month: float = 0.0,
    days_in_period: float | None = None,
) -> dict:
    """Actual energy cost over the window using the embedded per-step price.

    The dataset's ``price_eur_per_kwh`` is already the resolved retail price for
    both dynamic (spot+adder) and fixed homes, so we never re-add the adder here.
    Base fee is pro-rated by the fraction of the month covered.
    """
    if df.empty:
        return {"energy_cost_eur": 0.0, "feed_in_credit_eur": 0.0,
                "base_fee_eur": 0.0, "total_eur": 0.0}

    import_kwh = df["grid_import_kw"].fillna(0) * STEP_HOURS
    export_kwh = df["grid_export_kw"].fillna(0) * STEP_HOURS
    price = df["price_eur_per_kwh"].fillna(0)

    energy_cost_eur = float((import_kwh * price).sum())
    feed_in_credit = float((export_kwh * feed_in_eur_per_kwh).sum())

    base_fee = 0.0
    if base_fee_eur_per_month:
        # Pro-rate by the covered fraction of the calendar month the window sits in,
        # matching how monthly bills are computed (days_covered / days_in_month).
        first = df.index.min()
        days_in_month = calendar.monthrange(first.year, first.month)[1]
        if days_in_period is None:
            steps = len(df)
            days_in_period = steps * STEP_HOURS / 24
        base_fee = base_fee_eur_per_month * (days_in_period / days_in_month)

    total = energy_cost_eur - feed_in_credit + base_fee
    return {
        "energy_cost_eur": round(energy_cost_eur, 2),
        "feed_in_credit_eur": round(feed_in_credit, 2),
        "base_fee_eur": round(base_fee, 2),
        "total_eur": round(total, 2),
    }


def device_breakdown(df: pd.DataFrame) -> dict[str, float | None]:
    """Share of consumption (%) per device class. None when consumption is 0."""
    consumption = _sum_kwh(df, "total_consumption_kw")
    if consumption <= 0:
        return {"heatpump_pct": None, "ev_pct": None, "house_load_pct": None,
                "battery_charge_pct": None}
    return {
        "heatpump_pct": round(_sum_kwh(df, "heatpump_kw") / consumption * 100, 1),
        "ev_pct": round(_sum_kwh(df, "ev_charging_kw") / consumption * 100, 1),
        "house_load_pct": round(_sum_kwh(df, "house_load_kw") / consumption * 100, 1),
        "battery_charge_pct": round(_sum_kwh(df, "battery_charge_kw") / consumption * 100, 1),
    }


def baseload_kw(df: pd.DataFrame, low_hour: int = 1, high_hour: int = 5) -> float | None:
    """Standby/baseload estimate: median house load during the quiet night hours."""
    if df.empty or "house_load_kw" not in df:
        return None
    night = df.between_time(f"{low_hour:02d}:00", f"{high_hour:02d}:00")
    if night.empty:
        return None
    return round(float(night["house_load_kw"].median()), 3)


def latest_status(df: pd.DataFrame) -> dict | None:
    """Most recent step's instantaneous readings — drives the live status card."""
    if df.empty:
        return None
    row = df.iloc[-1]
    return {
        "ts": df.index[-1].isoformat(),
        "pv_production_kw": round(float(row.get("pv_production_kw", 0) or 0), 3),
        "total_consumption_kw": round(float(row.get("total_consumption_kw", 0) or 0), 3),
        "grid_import_kw": round(float(row.get("grid_import_kw", 0) or 0), 3),
        "grid_export_kw": round(float(row.get("grid_export_kw", 0) or 0), 3),
        "battery_soc_pct": round(float(row.get("battery_soc_pct", 0) or 0), 1),
        "outdoor_temp_c": (None if pd.isna(row.get("outdoor_temp_c"))
                           else round(float(row.get("outdoor_temp_c")), 1)),
        "price_eur_per_kwh": (None if pd.isna(row.get("price_eur_per_kwh"))
                              else round(float(row.get("price_eur_per_kwh")), 4)),
    }


def period_comparison(
    current: dict, previous: dict, key: str = "total_eur"
) -> dict:
    """Delta vs the previous equal-length period for a cost/energy metric."""
    cur, prev = current.get(key) or 0.0, previous.get(key) or 0.0
    delta = cur - prev
    pct = (delta / prev * 100) if prev else None
    return {"current": round(cur, 2), "previous": round(prev, 2),
            "delta_eur": round(delta, 2),
            "delta_pct": (round(pct, 1) if pct is not None else None)}
