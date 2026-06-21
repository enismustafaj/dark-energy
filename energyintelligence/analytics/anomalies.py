"""Rule-based anomaly / nudge / insight detectors.

Each detector consumes telemetry (+ reference tables) and emits a structured
``Fact`` whose ``numbers`` are the only figures the AI layer may surface. The
four detectors mirror the seeded ``insight_events.json`` categories:

  * heat-pump overconsumption vs a temperature-conditioned baseline
  * high standby / baseload
  * bill-spike month (outlier vs the home's own median)
  * cheapest-charging-window nudge (from dynamic spot prices)

Thresholds derive from each home's own distribution, never from fixed dates, so
the same code works on any dataset/timeframe. Detectors are skipped when the
relevant asset is absent (e.g. no heat pump on HH-1004).
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from ..models import Fact
from . import frames

HP_RATIO_THRESHOLD = 1.15   # sustained ratio vs trailing norm needed to flag (+15%)
HP_RUN_WINDOW_DAYS = 7      # rolling window used to smooth the daily signal
HP_MIN_RUN_DAYS = 5        # minimum contiguous days for a real fault


def detect_heatpump_overconsumption(
    conn: sqlite3.Connection, household_id: str, has_heat_pump: bool
) -> list[Fact]:
    """Flag a sustained window where heat-pump use exceeds the temperature-
    conditioned baseline (possible fault / misconfiguration)."""
    if not has_heat_pump:
        return []
    df = frames.load_window(conn, household_id)
    if df.empty or df["heatpump_kw"].fillna(0).sum() == 0:
        return []

    daily = pd.DataFrame({
        "hp_kwh": df["heatpump_kw"].resample("1D").sum() * 0.25,
        "temp": df["outdoor_temp_c"].resample("1D").mean(),
    }).dropna()
    if len(daily) < 14:
        return []

    # Temperature-normalise usage (1.0 == typical for that temperature), then
    # compare to the home's own trailing 4-week median of that normalised value.
    # A sustained ratio > 1 means "using more than usual" even after accounting
    # for weather — the signature of a fault rather than a cold/hot spell.
    daily["tbin"] = daily["temp"].round()
    typical = daily.groupby("tbin")["hp_kwh"].transform("median")
    daily["norm"] = daily["hp_kwh"] / typical.replace(0, np.nan)
    daily["trail"] = daily["norm"].rolling(28, min_periods=10).median().shift(1)
    daily["ratio"] = daily["norm"] / daily["trail"]
    daily["roll"] = (daily["ratio"]
                     .rolling(HP_RUN_WINDOW_DAYS, center=True, min_periods=4).mean())

    if daily["roll"].max(skipna=True) < HP_RATIO_THRESHOLD:
        return []

    peak_day = daily["roll"].idxmax()
    # Delimit the run while the smoothed ratio stays above the midpoint of
    # threshold and 1.0 — robust to single noisy days dipping under.
    floor = 1.0 + (HP_RATIO_THRESHOLD - 1.0) / 2
    over = daily["roll"] > floor
    idx = list(daily.index)
    p = idx.index(peak_day)
    lo = hi = p
    while lo > 0 and bool(over.iloc[lo - 1]):
        lo -= 1
    while hi < len(idx) - 1 and bool(over.iloc[hi + 1]):
        hi += 1
    start, end = idx[lo], idx[hi]
    excess = round(float((daily.loc[start:end, "ratio"].mean() - 1) * 100), 0)
    days = int((end - start).days) + 1
    if days < HP_MIN_RUN_DAYS:
        return []

    fact = Fact(
        key="heatpump_overconsumption",
        household_id=household_id,
        type="anomaly",
        severity="high",
        period=f"{start.date()}..{end.date()}",
        title="Heat pump consumed more than expected",
        detail=(f"Heat-pump electricity use ran about {excess:.0f}% above the "
                f"temperature-adjusted norm for {days} days — possibly a fault, "
                f"low refrigerant, or thermostat misconfiguration."),
        numbers={"excess_pct": excess, "days": days},
        template_id="heatpump_overconsumption",
        suggested_action_key="shift_heatpump_to_cheap_window",
    )
    return [fact]


def detect_high_baseload(conn: sqlite3.Connection, household_id: str) -> list[Fact]:
    """Flag elevated night-time standby relative to the home's daily consumption."""
    df = frames.load_window(conn, household_id)
    if df.empty:
        return []
    night = df.between_time("01:00", "05:00")
    if night.empty:
        return []
    baseload = float(night["house_load_kw"].median())
    daily_avg = float(df["house_load_kw"].mean())
    if daily_avg <= 0:
        return []
    ratio = baseload / daily_avg
    # Standby above 60% of the all-hours average load is suspiciously high.
    if ratio < 0.6:
        return []
    nightly_kwh = round(baseload * 24 * 0.25 * 4, 1)  # baseload kW over the night band approx
    annual_kwh = round(baseload * 8760, 0)
    fact = Fact(
        key="high_baseload",
        household_id=household_id,
        type="nudge",
        severity="info",
        period="recurring",
        title="High always-on standby load",
        detail=(f"Your overnight baseload is about {baseload:.2f} kW, roughly "
                f"{ratio*100:.0f}% of your average load — around {annual_kwh:.0f} kWh/year "
                f"of always-on draw worth investigating."),
        numbers={"baseload_kw": round(baseload, 2), "ratio_pct": round(ratio * 100, 0),
                 "annual_kwh": annual_kwh},
        template_id="high_baseload",
        suggested_action_key=None,
    )
    return [fact]


def detect_bill_spike(conn: sqlite3.Connection, household_id: str) -> list[Fact]:
    """Flag the month whose total bill is a strong high outlier for this home."""
    rows = conn.execute(
        "SELECT month, total_bill_eur FROM monthly_bills WHERE household_id=? ORDER BY month",
        (household_id,),
    ).fetchall()
    if len(rows) < 3:
        return []
    bills = pd.Series({r["month"]: r["total_bill_eur"] for r in rows})
    hi_month = bills.idxmax()
    lo_month = bills.idxmin()
    hi, lo, med = bills.max(), bills.min(), bills.median()
    if med <= 0 or hi < med * 1.5:
        return []
    fact = Fact(
        key="bill_spike",
        household_id=household_id,
        type="insight",
        severity="info",
        period=hi_month,
        title=f"Highest bill in {hi_month}",
        detail=(f"{hi_month} cost €{hi:.2f} versus your low of €{lo:.2f} in {lo_month}, "
                f"driven by seasonal demand and lower solar."),
        numbers={"high_eur": round(float(hi), 2), "low_eur": round(float(lo), 2),
                 "high_month": hi_month, "low_month": lo_month},
        template_id="bill_spike",
        suggested_action_key="shift_heatpump_to_cheap_window",
    )
    return [fact]


def hourly_retail_prices(conn: sqlite3.Connection, tariff_id: str) -> pd.Series | None:
    """Average retail price (spot + tariff adder) by hour-of-day over the most
    recent ~7 days of dynamic prices. Indexed 0–23. None if no price data.

    Shared by the cheapest-window detector and the dashboard's price visualization
    so both read the same numbers.
    """
    rows = conn.execute(
        "SELECT ts, spot_price_eur_per_kwh FROM dynamic_prices ORDER BY ts DESC LIMIT 168"
    ).fetchall()
    if not rows:
        return None
    s = pd.Series(
        {pd.to_datetime(r["ts"]): r["spot_price_eur_per_kwh"] for r in rows}
    ).sort_index()
    tariff = conn.execute(
        "SELECT spot_adder_eur_per_kwh, energy_rate_eur_per_kwh, type FROM tariffs WHERE tariff_id=?",
        (tariff_id,),
    ).fetchone()
    adder = (tariff["spot_adder_eur_per_kwh"] or 0.0) if tariff else 0.0
    return (s + adder).groupby((s + adder).index.hour).mean()


def detect_cheapest_window(
    conn: sqlite3.Connection, household_id: str, tariff_id: str
) -> list[Fact]:
    """Find the cheapest hour-of-day from recent dynamic prices and nudge to shift
    flexible loads there. Handles negative/zero prices."""
    by_hour = hourly_retail_prices(conn, tariff_id)
    if by_hour is None:
        return []
    cheap_hour = int(by_hour.idxmin())
    cheap_price = round(float(by_hour.min()), 3)
    fact = Fact(
        key="cheapest_window",
        household_id=household_id,
        type="nudge",
        severity="info",
        period="recurring",
        title=f"Cheapest power is around {cheap_hour:02d}:00",
        detail=(f"Over the last week the lowest average price was at {cheap_hour:02d}:00 "
                f"(~€{cheap_price:.3f}/kWh). Shift flexible loads (EV, dishwasher, laundry) "
                f"into this window."),
        numbers={"cheap_hour": cheap_hour, "cheap_price_eur": cheap_price},
        template_id="cheapest_window",
        suggested_action_key="schedule_ev_charge",
    )
    return [fact]


def detect_all(conn: sqlite3.Connection, household_id: str) -> list[Fact]:
    """Run every applicable detector for a household and return all Facts."""
    h = conn.execute(
        "SELECT heat_pump, tariff_id FROM households WHERE household_id=?",
        (household_id,),
    ).fetchone()
    if h is None:
        return []
    facts: list[Fact] = []
    facts += detect_heatpump_overconsumption(conn, household_id, bool(h["heat_pump"]))
    facts += detect_high_baseload(conn, household_id)
    facts += detect_bill_spike(conn, household_id)
    facts += detect_cheapest_window(conn, household_id, h["tariff_id"])
    return facts
