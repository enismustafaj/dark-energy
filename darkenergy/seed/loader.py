"""Seed loader — populates SQLite from a dataset directory.

Format-driven and year-agnostic: households and their timeseries files are
discovered from ``households.json``; the timeframe is whatever the records span.
Pointing ``DARKENERGY_DATASET_DIR`` at a differently-dated dataset Just Works.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings
from ..db import connect, init_db, transaction
from ..models import TelemetryRecord

BATCH = 5000

TELEMETRY_COLUMNS = (
    "household_id", "ts", "outdoor_temp_c", "pv_production_kw", "house_load_kw",
    "heatpump_kw", "ev_charging_kw", "total_consumption_kw", "battery_charge_kw",
    "battery_discharge_kw", "battery_soc_kwh", "battery_soc_pct", "grid_import_kw",
    "grid_export_kw", "price_eur_per_kwh", "source",
)


def _load_json(path: Path):
    with path.open() as f:
        return json.load(f)


def _seed_households(conn: sqlite3.Connection, dataset_dir: Path) -> list[dict]:
    households = _load_json(dataset_dir / "households.json")
    rows = [
        (
            h["household_id"], h["name"], h["city"], h["residents"], h["pv_kwp"],
            h["battery_kwh"], h["battery_power_kw"], int(bool(h["heat_pump"])),
            int(bool(h["ev_charger"])), h["tariff_id"],
        )
        for h in households
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO households (household_id,name,city,residents,pv_kwp,"
        "battery_kwh,battery_power_kw,heat_pump,ev_charger,tariff_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return households


def _seed_tariffs(conn: sqlite3.Connection, dataset_dir: Path) -> None:
    tariffs = _load_json(dataset_dir / "tariffs.json")
    rows = [
        (
            t["tariff_id"], t.get("name"), t.get("type"),
            t.get("spot_adder_eur_per_kwh"), t.get("energy_rate_eur_per_kwh"),
            t.get("base_fee_eur_per_month"), t.get("feed_in_eur_per_kwh"),
            json.dumps(t),
        )
        for t in tariffs
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO tariffs (tariff_id,name,type,spot_adder_eur_per_kwh,"
        "energy_rate_eur_per_kwh,base_fee_eur_per_month,feed_in_eur_per_kwh,raw_json) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )


def _seed_contracts(conn: sqlite3.Connection, dataset_dir: Path) -> None:
    contracts = _load_json(dataset_dir / "contracts.json")
    rows = []
    for c in contracts:
        pricing = c.get("energy_pricing", {})
        rows.append((
            c["household_id"], c.get("tariff_id"), c.get("contract_start"),
            c.get("contract_end"), c.get("minimum_term_months"),
            c.get("notice_period_weeks"), c.get("auto_renew_months"),
            c.get("base_fee_eur_per_month"), pricing.get("model"),
            c.get("feed_in_eur_per_kwh"), json.dumps(c.get("assets", {})),
            c.get("contract_terms_text"),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO contracts (household_id,tariff_id,contract_start,"
        "contract_end,minimum_term_months,notice_period_weeks,auto_renew_months,"
        "base_fee_eur_per_month,pricing_model,feed_in_eur_per_kwh,assets_json,"
        "contract_terms_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def _seed_dynamic_prices(conn: sqlite3.Connection, dataset_dir: Path) -> int:
    data = _load_json(dataset_dir / "dynamic_prices.json")
    prices = data["prices"] if isinstance(data, dict) else data
    rows = [(p["timestamp"], p["spot_price_eur_per_kwh"]) for p in prices]
    conn.executemany(
        "INSERT OR REPLACE INTO dynamic_prices (ts,spot_price_eur_per_kwh) VALUES (?,?)",
        rows,
    )
    return len(rows)


def _seed_monthly_bills(conn: sqlite3.Connection, dataset_dir: Path) -> int:
    bills = _load_json(dataset_dir / "monthly_bills.json")
    rows = [
        (
            b["household_id"], b["month"], b.get("consumption_kwh"),
            b.get("pv_production_kwh"), b.get("grid_import_kwh"),
            b.get("grid_export_kwh"), b.get("energy_cost_eur"),
            b.get("base_fee_eur"), b.get("feed_in_credit_eur"),
            b.get("total_bill_eur"), b.get("self_sufficiency_pct"),
        )
        for b in bills
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO monthly_bills (household_id,month,consumption_kwh,"
        "pv_production_kwh,grid_import_kwh,grid_export_kwh,energy_cost_eur,base_fee_eur,"
        "feed_in_credit_eur,total_bill_eur,self_sufficiency_pct) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _seed_insight_events(conn: sqlite3.Connection, dataset_dir: Path) -> int:
    path = dataset_dir / "insight_events.json"
    if not path.exists():
        return 0
    events = _load_json(path)
    now = datetime.now(timezone.utc).isoformat()
    # Clear previously-seeded events to stay idempotent (detected ones are kept).
    conn.execute("DELETE FROM insight_events WHERE origin = 'seed'")
    rows = [
        (
            e["household_id"], e.get("type"), e.get("severity"), e.get("period"),
            e.get("title"), e.get("detail"), e.get("suggested_action"), "seed", now,
        )
        for e in events
    ]
    conn.executemany(
        "INSERT INTO insight_events (household_id,type,severity,period,title,detail,"
        "suggested_action,origin,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _seed_telemetry(conn: sqlite3.Connection, household: dict, dataset_dir: Path) -> int:
    """Bulk-insert one household's timeseries. Uses a parsed dataset record per row
    via the unified model so seed and live share validation."""
    ts_file = dataset_dir / household["timeseries_file"]
    payload = _load_json(ts_file)
    records = payload["records"] if isinstance(payload, dict) else payload
    hh = household["household_id"]

    batch: list[tuple] = []
    inserted = 0

    def flush():
        nonlocal inserted
        if not batch:
            return
        conn.executemany(
            f"INSERT OR REPLACE INTO telemetry ({','.join(TELEMETRY_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(TELEMETRY_COLUMNS))})",
            batch,
        )
        inserted += len(batch)
        batch.clear()

    for rec in records:
        r = TelemetryRecord.from_dataset_record(hh, rec)
        batch.append((
            r.household_id, r.ts.isoformat(), r.outdoor_temp_c, r.pv_production_kw,
            r.house_load_kw, r.heatpump_kw, r.ev_charging_kw, r.total_consumption_kw,
            r.battery_charge_kw, r.battery_discharge_kw, r.battery_soc_kwh,
            r.battery_soc_pct, r.grid_import_kw, r.grid_export_kw, r.price_eur_per_kwh,
            "seed",
        ))
        if len(batch) >= BATCH:
            flush()
    flush()
    return inserted


def _seed_catalog(conn: sqlite3.Connection, dataset_dir: Path) -> int:
    path = dataset_dir / "qualified_appliances.json"
    if not path.exists():
        return 0
    data = _load_json(path)
    items = data["appliances"] if isinstance(data, dict) else data
    conn.execute("DELETE FROM appliance_catalog")
    rows = [
        (a["id"], a["category"], a.get("make_model"), a.get("capacity_kwh"),
         a.get("power_kw"), a.get("rated_kw"), a.get("efficiency"), a.get("capex_eur"),
         json.dumps(a.get("specs_json", {})))
        for a in items
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO appliance_catalog (id,category,make_model,capacity_kwh,"
        "power_kw,rated_kw,efficiency,capex_eur,specs_json) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def _seed_devices(conn: sqlite3.Connection, dataset_dir: Path) -> int:
    """Derive the canonical devices table from contract asset specs (richer than
    the household table — carries SCOP, EV battery, charge power)."""
    contracts = {c["household_id"]: c for c in _load_json(dataset_dir / "contracts.json")}
    households = _load_json(dataset_dir / "households.json")
    conn.execute("DELETE FROM devices")
    rows: list[tuple] = []

    def add(hh, category, **kw):
        rows.append((
            hh, category, kw.get("make_model"), kw.get("capacity_kwh"),
            kw.get("power_kw"), kw.get("rated_kw"), kw.get("efficiency"),
            kw.get("installed_year"), kw.get("telemetry_channel"),
            json.dumps(kw.get("specs", {})),
        ))

    for h in households:
        hh = h["household_id"]
        assets = contracts.get(hh, {}).get("assets", {})
        # Every home has aggregate household load + a grid connection (implicit).
        add(hh, "household", telemetry_channel="house_load_kw")
        if (assets.get("pv_kwp") or h.get("pv_kwp") or 0) > 0:
            add(hh, "pv", rated_kw=assets.get("pv_kwp", h.get("pv_kwp")),
                telemetry_channel="pv_production_kw")
        if (assets.get("battery_kwh") or h.get("battery_kwh") or 0) > 0:
            add(hh, "battery", capacity_kwh=assets.get("battery_kwh", h.get("battery_kwh")),
                power_kw=assets.get("battery_power_kw", h.get("battery_power_kw")),
                efficiency=0.90,
                telemetry_channel="battery_charge_kw,battery_discharge_kw,battery_soc_pct")
        if assets.get("heat_pump", h.get("heat_pump")):
            add(hh, "heat_pump", rated_kw=assets.get("heat_pump_kw"),
                efficiency=3.2, telemetry_channel="heatpump_kw")  # assumed baseline SCOP
        if assets.get("ev_charger", h.get("ev_charger")):
            add(hh, "ev_charger", rated_kw=11.0, efficiency=0.92,
                telemetry_channel="ev_charging_kw")
            add(hh, "ev", capacity_kwh=assets.get("ev_battery_kwh"),
                telemetry_channel="ev_charging_kw")

    conn.executemany(
        "INSERT INTO devices (household_id,category,make_model,capacity_kwh,power_kw,"
        "rated_kw,efficiency,installed_year,telemetry_channel,specs_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def seed(dataset_dir: Path | None = None, db_path: Path | None = None) -> dict[str, int]:
    """Populate the database from a dataset directory. Returns row counts."""
    settings = get_settings()
    dataset_dir = dataset_dir or settings.dataset_dir
    conn = connect(db_path)
    init_db(conn)

    counts: dict[str, int] = {}
    # Speed up the bulk telemetry load; restored after.
    conn.execute("PRAGMA synchronous=OFF")
    try:
        with transaction(conn):
            households = _seed_households(conn, dataset_dir)
            _seed_tariffs(conn, dataset_dir)
            _seed_contracts(conn, dataset_dir)
            counts["households"] = len(households)
            counts["dynamic_prices"] = _seed_dynamic_prices(conn, dataset_dir)
            counts["monthly_bills"] = _seed_monthly_bills(conn, dataset_dir)
            counts["insight_events"] = _seed_insight_events(conn, dataset_dir)
            counts["devices"] = _seed_devices(conn, dataset_dir)
            counts["appliance_catalog"] = _seed_catalog(conn, dataset_dir)

        total_tel = 0
        for h in households:
            with transaction(conn):
                total_tel += _seed_telemetry(conn, h, dataset_dir)
        counts["telemetry"] = total_tel
    finally:
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.close()
    return counts
