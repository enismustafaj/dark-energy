"""The single telemetry read path: tenant + time range -> pandas DataFrame.

Every analytics function consumes a frame produced here, which is what keeps
historical-seed and rolling-live analysis identical and tenant-scoped — there is
no query that does not require a ``household_id``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

STEP_HOURS = 0.25  # 15-minute resolution

NUMERIC_COLS = [
    "outdoor_temp_c", "pv_production_kw", "house_load_kw", "heatpump_kw",
    "ev_charging_kw", "total_consumption_kw", "battery_charge_kw",
    "battery_discharge_kw", "battery_soc_kwh", "battery_soc_pct",
    "grid_import_kw", "grid_export_kw", "price_eur_per_kwh",
]


def load_window(
    conn: sqlite3.Connection,
    household_id: str,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
) -> pd.DataFrame:
    """Load a tenant's telemetry over [start, end). Bounds optional → full range.

    Returns a DataFrame indexed by a tz-naive ``ts`` DatetimeIndex, with all
    kWh-derived helper columns. Empty frame (correct columns) if no rows.
    """
    clauses = ["household_id = ?"]
    params: list = [household_id]
    if start is not None:
        clauses.append("ts >= ?")
        params.append(start.isoformat() if isinstance(start, datetime) else start)
    if end is not None:
        clauses.append("ts < ?")
        params.append(end.isoformat() if isinstance(end, datetime) else end)

    sql = (
        "SELECT ts, " + ", ".join(NUMERIC_COLS) +
        " FROM telemetry WHERE " + " AND ".join(clauses) + " ORDER BY ts"
    )
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        df = pd.DataFrame(columns=["ts"] + NUMERIC_COLS)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts")
    for c in NUMERIC_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # Derived kWh-per-step helpers (kW * 0.25h).
    for c in ("pv_production_kw", "total_consumption_kw", "grid_import_kw",
              "grid_export_kw", "heatpump_kw", "ev_charging_kw", "house_load_kw"):
        df[c.replace("_kw", "_kwh")] = df[c] * STEP_HOURS
    return df
