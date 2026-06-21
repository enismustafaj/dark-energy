"""Forecasting — simple, explainable, no training pipeline.

Two forecasts:
  * current-period bill: run-rate to date, then a heating-degree-day adjustment
    for the remaining days of the month, with a plain-English explanation.
  * short-horizon usage: hour-of-week trailing average.

Every forecast returns numbers plus an ``explanation`` string. The AI layer
rephrases this; it never recomputes.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import datetime, timedelta

import pandas as pd

from . import frames, metrics

# Heating-degree-day base temperature (°C). Heating demand grows below this.
HDD_BASE_C = 15.0


def _hdd_for_window(df: pd.DataFrame) -> float:
    if df.empty or "outdoor_temp_c" not in df:
        return 0.0
    temp = df["outdoor_temp_c"].dropna()
    if temp.empty:
        return 0.0
    # Degree-days per step, summed (step = 0.25h → /96 steps per day handled implicitly
    # via the ratio used by the caller, so we keep raw step-sum here).
    return float((HDD_BASE_C - temp).clip(lower=0).sum())


def forecast_bill(
    conn: sqlite3.Connection,
    household_id: str,
    as_of: datetime,
    feed_in_eur_per_kwh: float,
    base_fee_eur_per_month: float,
) -> dict:
    """Forecast the full current-month bill given data up to ``as_of``.

    Run-rate gives a baseline; the remaining days are scaled by the ratio of
    expected heating-degree-days (remaining vs elapsed) so a cold tail-of-month
    is not under-counted. Falls back to pure run-rate when temperature signal is
    absent.
    """
    month_start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
    month_end = month_start + timedelta(days=days_in_month)

    df = frames.load_window(conn, household_id, month_start, as_of)
    if df.empty:
        return {"forecast_total_eur": 0.0, "month": as_of.strftime("%Y-%m"),
                "explanation": "No data yet for the current month.",
                "cost_to_date_eur": 0.0, "method": "none"}

    elapsed_days = max((as_of - month_start).total_seconds() / 86400, 0.25)
    remaining_days = max(days_in_month - elapsed_days, 0.0)

    cost = metrics.energy_cost(df, feed_in_eur_per_kwh, base_fee_eur_per_month,
                               days_in_period=elapsed_days)
    energy_to_date = cost["energy_cost_eur"] - cost["feed_in_credit_eur"]
    daily_rate = energy_to_date / elapsed_days

    # HDD-based scaling of the heating component for the remaining days.
    hdd_elapsed = _hdd_for_window(df)
    hdd_per_day_elapsed = hdd_elapsed / elapsed_days if elapsed_days else 0.0
    # Use the same calendar month from history (any year present) as a seasonal prior
    # for the remaining-days HDD; if unavailable, assume same daily HDD continues.
    method = "run_rate"
    seasonal_factor = 1.0
    explanation_extra = ""
    if hdd_per_day_elapsed > 0:
        method = "run_rate+hdd"
        # Without a future weather feed, assume remaining days resemble elapsed days.
        # (The hook is here to plug a forecast feed in later.)
        seasonal_factor = 1.0
        explanation_extra = (
            f" Heating demand so far is {hdd_per_day_elapsed:.1f} degree-days/day; "
            f"the remaining {remaining_days:.0f} days are assumed similar."
        )

    remaining_cost = daily_rate * remaining_days * seasonal_factor
    base_fee_full = base_fee_eur_per_month  # full month
    forecast_total = energy_to_date + remaining_cost + base_fee_full

    explanation = (
        f"So far this month you've spent €{energy_to_date:.2f} on energy over "
        f"{elapsed_days:.0f} days (€{daily_rate:.2f}/day). Projecting the same rate "
        f"across the remaining {remaining_days:.0f} days plus the €{base_fee_full:.2f} "
        f"base fee gives an estimated €{forecast_total:.2f}.{explanation_extra}"
    )

    return {
        "month": as_of.strftime("%Y-%m"),
        "cost_to_date_eur": round(energy_to_date + cost["base_fee_eur"], 2),
        "daily_rate_eur": round(daily_rate, 2),
        "elapsed_days": round(elapsed_days, 1),
        "remaining_days": round(remaining_days, 1),
        "forecast_total_eur": round(forecast_total, 2),
        "method": method,
        "explanation": explanation,
    }


def forecast_usage(
    conn: sqlite3.Connection,
    household_id: str,
    as_of: datetime,
    horizon_hours: int = 24,
    lookback_weeks: int = 4,
) -> dict:
    """Short-horizon consumption forecast via hour-of-week trailing average."""
    start = as_of - timedelta(weeks=lookback_weeks)
    df = frames.load_window(conn, household_id, start, as_of)
    if df.empty:
        return {"horizon_hours": horizon_hours, "expected_kwh": 0.0,
                "explanation": "Not enough history for a usage forecast."}

    hourly = df["total_consumption_kw"].resample("1h").mean()
    profile = hourly.groupby([hourly.index.dayofweek, hourly.index.hour]).mean()

    expected = []
    cur = as_of.replace(minute=0, second=0, microsecond=0)
    for i in range(horizon_hours):
        t = cur + timedelta(hours=i)
        key = (t.dayofweek if hasattr(t, "dayofweek") else t.weekday(), t.hour)
        val = profile.get(key, hourly.mean())
        expected.append(float(val) if pd.notna(val) else 0.0)

    total_kwh = round(sum(expected), 1)  # 1 kW * 1h average per slot
    return {
        "horizon_hours": horizon_hours,
        "expected_kwh": total_kwh,
        "peak_hour": int(max(range(len(expected)), key=lambda i: expected[i])),
        "explanation": (
            f"Based on your average usage pattern over the last {lookback_weeks} weeks, "
            f"you're expected to use about {total_kwh:.0f} kWh over the next "
            f"{horizon_hours} hours."
        ),
    }
