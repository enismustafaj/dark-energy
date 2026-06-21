"""Ingest: unified merge holds the energy balance and is tenant-scoped."""

from __future__ import annotations

from energyintelligence.ingest.mapping import reading_to_record
from energyintelligence.models import DeviceReading, TelemetryRecord


def test_device_reading_merge_holds_balance():
    reading = DeviceReading(
        household_id="HH-1001", device_type="pv", ts="2025-06-01T12:00:00",
        metrics={"pv_production_kw": 6.0},
    )
    prev = {
        "outdoor_temp_c": 20.0, "pv_production_kw": 0.0, "house_load_kw": 1.0,
        "heatpump_kw": 0.5, "ev_charging_kw": 0.0, "total_consumption_kw": 1.5,
        "battery_charge_kw": 0.0, "battery_discharge_kw": 0.0, "battery_soc_kwh": 5.0,
        "battery_soc_pct": 50.0, "grid_import_kw": 1.5, "grid_export_kw": 0.0,
        "price_eur_per_kwh": 0.30,
    }
    rec = reading_to_record(reading, prev)
    assert rec.source == "live"
    assert rec.pv_production_kw == 6.0
    # 6 pv, 1.5 consumption -> 4.5 surplus exported, 0 import
    assert rec.grid_export_kw == 4.5
    assert rec.grid_import_kw == 0.0
    assert rec.balance_residual() < 1e-9


def test_snapshot_ts_snaps_to_grid():
    rec = TelemetryRecord(household_id="HH-1001", ts="2025-06-01T12:07:33")
    assert rec.ts.minute == 0  # 12:07 floors to 12:00 on the 15-min grid
    assert rec.ts.second == 0
