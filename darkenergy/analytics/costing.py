"""Counterfactual cost engine.

One reusable primitive — ``replay_cost`` — re-runs the energy-cost ETL over a
household's *real* telemetry under a modified assumption supplied by a rule:

  * ``transform(df) -> df`` mutates the telemetry first (shift flexible load into
    cheap hours, scale heat-pump draw by an efficiency ratio, dispatch a battery
    against grid import, …);
  * ``price_series`` / ``flat_rate`` reprice grid imports under a different tariff.

A rule computes its advice's benefit as ``baseline_cost - counterfactual_cost``.
Everything here is a pure function over a DataFrame — no DB writes, no surprises.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from .frames import STEP_HOURS

Transform = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass
class CostResult:
    energy_cost_eur: float
    feed_in_credit_eur: float
    base_fee_eur: float
    total_eur: float

    def as_dict(self) -> dict:
        return {
            "energy_cost_eur": round(self.energy_cost_eur, 2),
            "feed_in_credit_eur": round(self.feed_in_credit_eur, 2),
            "base_fee_eur": round(self.base_fee_eur, 2),
            "total_eur": round(self.total_eur, 2),
        }


def _months_spanned(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    span_days = (df.index.max() - df.index.min()).total_seconds() / 86400 + STEP_HOURS / 24
    return max(span_days / 30.0, STEP_HOURS / 24)


def replay_cost(
    df: pd.DataFrame,
    *,
    feed_in_eur_per_kwh: float,
    base_fee_eur_per_month: float = 0.0,
    price_series: pd.Series | None = None,
    flat_rate: float | None = None,
    transform: Transform | None = None,
) -> CostResult:
    """Cost the telemetry, optionally transformed and/or repriced.

    Pricing precedence: ``flat_rate`` > ``price_series`` (indexed to the data's
    hour) > the telemetry's own ``price_eur_per_kwh`` column (the actual retail
    price already resolved per home).
    """
    if df.empty:
        return CostResult(0.0, 0.0, 0.0, 0.0)

    work = transform(df.copy()) if transform else df

    import_kwh = work["grid_import_kw"].fillna(0) * STEP_HOURS
    export_kwh = work["grid_export_kw"].fillna(0) * STEP_HOURS

    if flat_rate is not None:
        price = pd.Series(flat_rate, index=work.index)
    elif price_series is not None:
        # Map each step to the hourly price (floor to the hour).
        hourly = price_series.copy()
        fallback = work["price_eur_per_kwh"].fillna(method="ffill").mean()
        price = pd.Series(
            [hourly.get(t.replace(minute=0, second=0), fallback) for t in work.index],
            index=work.index,
        )
    else:
        price = work["price_eur_per_kwh"].fillna(0)

    energy_cost = float((import_kwh * price).sum())
    feed_in = float((export_kwh * feed_in_eur_per_kwh).sum())
    base_fee = base_fee_eur_per_month * _months_spanned(work)
    return CostResult(energy_cost, feed_in, base_fee, energy_cost - feed_in + base_fee)


# --- common transforms rules compose ---------------------------------------

def scale_device_load(column: str, factor: float) -> Transform:
    """Scale one device's draw by ``factor`` and re-derive grid import/export.

    Used by efficiency advice (e.g. a heat pump with a better SCOP draws less
    electricity for the same heat). Keeps the energy balance consistent.
    """
    def _t(df: pd.DataFrame) -> pd.DataFrame:
        delta = df[column].fillna(0) * (factor - 1.0)   # change in consumption (kW); <0 for a reduction
        df[column] = df[column].fillna(0) * factor
        df["total_consumption_kw"] = df["total_consumption_kw"].fillna(0) + delta
        # Apply the consumption change to the grid balance: a reduction (delta<0)
        # cuts import first, then spills into export; an increase does the reverse.
        net = df["grid_import_kw"].fillna(0) - df["grid_export_kw"].fillna(0) + delta
        df["grid_import_kw"] = net.clip(lower=0)
        df["grid_export_kw"] = (-net).clip(lower=0)
        return df
    return _t
