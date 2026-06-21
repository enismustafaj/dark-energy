"""Map per-device readings into the unified telemetry record.

A device reports only its own slice (``DeviceReading``); this merges that slice
onto the household's most recent record (for continuity of the other devices'
values), then recomputes the derived totals and the grid import/export split
from the energy-balance equation. The result is a normal ``TelemetryRecord`` —
the same shape the seed loader produces — so analytics never distinguishes them.
"""

from __future__ import annotations

import sqlite3

from ..models import DEVICE_COLUMNS, DeviceReading, TelemetryRecord


def _recompute_derived(rec: TelemetryRecord) -> None:
    """Set total consumption and the grid split so the balance equation holds."""
    rec.total_consumption_kw = (
        rec.house_load_kw + rec.heatpump_kw + rec.ev_charging_kw
    )
    # pv + import + discharge = consumption + export + charge
    surplus = (rec.pv_production_kw + rec.battery_discharge_kw
               - rec.total_consumption_kw - rec.battery_charge_kw)
    rec.grid_export_kw = max(surplus, 0.0)
    rec.grid_import_kw = max(-surplus, 0.0)


def reading_to_record(
    reading: DeviceReading, prev: sqlite3.Row | None
) -> TelemetryRecord:
    """Build a full record from one device reading, carrying prior values forward."""
    base: dict = {}
    if prev is not None:
        base = {k: prev[k] for k in prev.keys() if k not in ("household_id", "ts", "source")}
    rec = TelemetryRecord(household_id=reading.household_id, ts=reading.ts,
                          source="live", **base)
    # Apply this device's owned columns from its metrics.
    for col in DEVICE_COLUMNS[reading.device_type]:
        if col in reading.metrics:
            setattr(rec, col, reading.metrics[col])
    if reading.outdoor_temp_c is not None:
        rec.outdoor_temp_c = reading.outdoor_temp_c
    if reading.price_eur_per_kwh is not None:
        rec.price_eur_per_kwh = reading.price_eur_per_kwh
    _recompute_derived(rec)
    return rec


def merge_into_record(rec: TelemetryRecord, reading: DeviceReading) -> TelemetryRecord:
    """Apply a device reading onto an existing record in place (used for batching)."""
    for col in DEVICE_COLUMNS[reading.device_type]:
        if col in reading.metrics:
            setattr(rec, col, reading.metrics[col])
    if reading.outdoor_temp_c is not None:
        rec.outdoor_temp_c = reading.outdoor_temp_c
    if reading.price_eur_per_kwh is not None:
        rec.price_eur_per_kwh = reading.price_eur_per_kwh
    _recompute_derived(rec)
    return rec
