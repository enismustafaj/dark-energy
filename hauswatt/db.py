"""SQLite access layer: connection management, schema, tenant-scoped helpers.

The schema is the contract every other layer reads/writes through. ``telemetry``
is the single unified per-step table that BOTH the seed loader and the live
ingest endpoint write to, so analytics never knows whether a row is historical
or streamed. Every fact table leads its primary key / index with
``household_id`` — that is the tenant key, and there is no read path that does
not scope by it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
    household_id    TEXT PRIMARY KEY,
    name            TEXT,
    city            TEXT,
    residents       INTEGER,
    pv_kwp          REAL,
    battery_kwh     REAL,
    battery_power_kw REAL,
    heat_pump       INTEGER,   -- 0/1
    ev_charger      INTEGER,   -- 0/1
    tariff_id       TEXT
);

CREATE TABLE IF NOT EXISTS tariffs (
    tariff_id               TEXT PRIMARY KEY,
    name                    TEXT,
    type                    TEXT,   -- dynamic_hourly | fixed_rate
    spot_adder_eur_per_kwh  REAL,
    energy_rate_eur_per_kwh REAL,
    base_fee_eur_per_month  REAL,
    feed_in_eur_per_kwh     REAL,
    raw_json                TEXT
);

CREATE TABLE IF NOT EXISTS contracts (
    household_id        TEXT PRIMARY KEY REFERENCES households(household_id),
    tariff_id           TEXT,
    contract_start      TEXT,
    contract_end        TEXT,
    minimum_term_months INTEGER,
    notice_period_weeks INTEGER,
    auto_renew_months   INTEGER,
    base_fee_eur_per_month REAL,
    pricing_model       TEXT,
    feed_in_eur_per_kwh REAL,
    assets_json         TEXT,
    contract_terms_text TEXT
);

CREATE TABLE IF NOT EXISTS dynamic_prices (
    ts                      TEXT PRIMARY KEY,
    spot_price_eur_per_kwh  REAL
);

-- The unified per-step telemetry record. Seed AND live ingest both write here.
CREATE TABLE IF NOT EXISTS telemetry (
    household_id        TEXT NOT NULL,
    ts                  TEXT NOT NULL,   -- naive local ISO, on the resolution grid
    outdoor_temp_c      REAL,
    pv_production_kw    REAL,
    house_load_kw      REAL,
    heatpump_kw        REAL,
    ev_charging_kw     REAL,
    total_consumption_kw REAL,
    battery_charge_kw  REAL,
    battery_discharge_kw REAL,
    battery_soc_kwh    REAL,
    battery_soc_pct    REAL,
    grid_import_kw     REAL,
    grid_export_kw     REAL,
    price_eur_per_kwh  REAL,
    source             TEXT DEFAULT 'seed',   -- seed | live
    PRIMARY KEY (household_id, ts)
);

CREATE TABLE IF NOT EXISTS monthly_bills (
    household_id        TEXT NOT NULL,
    month               TEXT NOT NULL,   -- YYYY-MM
    consumption_kwh     REAL,
    pv_production_kwh   REAL,
    grid_import_kwh    REAL,
    grid_export_kwh    REAL,
    energy_cost_eur    REAL,
    base_fee_eur       REAL,
    feed_in_credit_eur REAL,
    total_bill_eur     REAL,
    self_sufficiency_pct REAL,
    PRIMARY KEY (household_id, month)
);

-- Canonical record of the devices/assets a household owns. Source of truth for
-- the rule engine, replacing contracts.assets_json. The 'household' synthetic
-- device represents aggregate base load.
CREATE TABLE IF NOT EXISTS devices (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id      TEXT NOT NULL,
    category          TEXT NOT NULL,  -- pv|battery|heat_pump|ev|ev_charger|household
    make_model        TEXT,
    capacity_kwh      REAL,           -- battery / EV battery capacity
    power_kw          REAL,           -- battery charge/discharge power
    rated_kw          REAL,           -- heat pump / charger rated power
    efficiency        REAL,           -- heat-pump SCOP, battery round-trip, etc.
    installed_year    INTEGER,
    telemetry_channel TEXT,           -- telemetry column(s) this device drives
    specs_json        TEXT
);
CREATE INDEX IF NOT EXISTS idx_devices_hh ON devices(household_id, category);

-- Dataset of qualified appliances backing device-choice advice.
CREATE TABLE IF NOT EXISTS appliance_catalog (
    id           TEXT PRIMARY KEY,
    category     TEXT NOT NULL,
    make_model   TEXT,
    capacity_kwh REAL,
    power_kw     REAL,
    rated_kw     REAL,
    efficiency   REAL,
    capex_eur    REAL,
    specs_json   TEXT
);

CREATE TABLE IF NOT EXISTS insight_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id    TEXT NOT NULL,
    type            TEXT,    -- anomaly | nudge | insight
    category        TEXT,    -- fault | contract | device_choice | utilization
    device_id       INTEGER, -- which device the advice is about (null = whole-home/contract)
    severity        TEXT,    -- info | warning | high
    period          TEXT,
    title           TEXT,
    detail          TEXT,
    suggested_action TEXT,
    benefit_eur     REAL,    -- annualized customer cost benefit, for ranking
    fact_key        TEXT,    -- links a detected insight to its Fact contract
    fact_json       TEXT,
    advice_json     TEXT,    -- structured advice + counterfactual breakdown
    phrased_text    TEXT,
    status          TEXT DEFAULT 'open',   -- open | resolved
    resolved_at     TEXT,
    origin          TEXT DEFAULT 'seed',   -- seed | detected
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_insights_hh ON insight_events(household_id, created_at);
-- A detected insight is unique per (household, fact_key, period): re-running
-- detection updates rather than duplicating.
CREATE UNIQUE INDEX IF NOT EXISTS idx_insights_dedupe
    ON insight_events(household_id, fact_key, period)
    WHERE origin = 'detected';

CREATE TABLE IF NOT EXISTS forecasts (
    household_id    TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    kind            TEXT NOT NULL,   -- bill | usage
    horizon         TEXT NOT NULL,
    value_json      TEXT,
    explanation     TEXT,
    PRIMARY KEY (household_id, as_of, kind, horizon)
);

CREATE TABLE IF NOT EXISTS actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id TEXT NOT NULL,
    action_type  TEXT,
    params_json  TEXT,
    status       TEXT,    -- pending | executed | failed
    effect_json  TEXT,
    created_at   TEXT,
    executed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_hh ON actions(household_id, created_at);

-- Precomputed dashboard advice, one row per household. Written off the request
-- path (on ingest / recompute) so GET /view is a pure read with no rule-engine
-- work. `payload_json` holds the exact ranked advice list the API returns.
CREATE TABLE IF NOT EXISTS advice_cache (
    household_id    TEXT PRIMARY KEY,
    payload_json    TEXT NOT NULL,
    computed_at     TEXT
);

-- Advice the household has already acted on. Each row is one applied
-- recommendation and the annual benefit it locked in, so the dashboard can show
-- realized (not just potential) savings. `benefit_eur` is the grounded annual
-- saving captured at the time the advice was applied.
CREATE TABLE IF NOT EXISTS applied_advice (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id  TEXT NOT NULL,
    fact_key      TEXT NOT NULL,
    title         TEXT,
    benefit_eur   REAL,
    applied_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_applied_hh ON applied_advice(household_id);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with sane pragmas for concurrent sim-writes + reads."""
    path = db_path or get_settings().db_path
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_column(conn, "insight_events", "status", "TEXT DEFAULT 'open'")
    _ensure_column(conn, "insight_events", "resolved_at", "TEXT")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --- tenant-scoped helpers -------------------------------------------------

def household_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT household_id FROM households ORDER BY household_id").fetchall()
    return [r["household_id"] for r in rows]


def get_household(conn: sqlite3.Connection, household_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM households WHERE household_id = ?", (household_id,)
    ).fetchone()


def household_exists(conn: sqlite3.Connection, household_id: str) -> bool:
    return get_household(conn, household_id) is not None


def get_tariff(conn: sqlite3.Connection, tariff_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tariffs WHERE tariff_id = ?", (tariff_id,)).fetchone()


def get_contract(conn: sqlite3.Connection, household_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM contracts WHERE household_id = ?", (household_id,)
    ).fetchone()


_TELEMETRY_COLS = (
    "household_id", "ts", "outdoor_temp_c", "pv_production_kw", "house_load_kw",
    "heatpump_kw", "ev_charging_kw", "total_consumption_kw", "battery_charge_kw",
    "battery_discharge_kw", "battery_soc_kwh", "battery_soc_pct", "grid_import_kw",
    "grid_export_kw", "price_eur_per_kwh", "source",
)


def upsert_telemetry(conn: sqlite3.Connection, record) -> None:
    """Insert/replace one unified telemetry record (a ``models.TelemetryRecord``).

    The single write path shared by the seed loader's batch insert and the live
    ingest endpoint, so both produce identical rows."""
    conn.execute(
        f"INSERT OR REPLACE INTO telemetry ({','.join(_TELEMETRY_COLS)}) "
        f"VALUES ({','.join('?' * len(_TELEMETRY_COLS))})",
        (
            record.household_id, record.ts.isoformat(), record.outdoor_temp_c,
            record.pv_production_kw, record.house_load_kw, record.heatpump_kw,
            record.ev_charging_kw, record.total_consumption_kw, record.battery_charge_kw,
            record.battery_discharge_kw, record.battery_soc_kwh, record.battery_soc_pct,
            record.grid_import_kw, record.grid_export_kw, record.price_eur_per_kwh,
            record.source,
        ),
    )
    conn.commit()


def upsert_detected_insight(conn: sqlite3.Connection, row: dict) -> dict:
    """Insert/replace a detected insight (deduped on household_id+fact_key+period)."""
    from datetime import datetime, timezone

    existing = conn.execute(
        "SELECT status, resolved_at FROM insight_events WHERE origin='detected' "
        "AND household_id=? AND fact_key=? AND period=?",
        (row["household_id"], row["fact_key"], row["period"]),
    ).fetchone()
    # Delete-then-insert keeps detection idempotent without relying on
    # partial-index ON CONFLICT semantics.
    conn.execute(
        "DELETE FROM insight_events WHERE origin='detected' AND household_id=? "
        "AND fact_key=? AND period=?",
        (row["household_id"], row["fact_key"], row["period"]),
    )
    full = {
        "category": None, "device_id": None, "benefit_eur": None, "advice_json": None,
        "status": existing["status"] if existing is not None and existing["status"] else "open",
        "resolved_at": existing["resolved_at"] if existing is not None else None,
        **row, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    conn.execute(
        "INSERT INTO insight_events (household_id,type,category,device_id,severity,period,"
        "title,detail,suggested_action,benefit_eur,fact_key,fact_json,advice_json,"
        "phrased_text,status,resolved_at,origin,created_at) "
        "VALUES (:household_id,:type,:category,:device_id,:severity,:period,:title,:detail,"
        ":suggested_action,:benefit_eur,:fact_key,:fact_json,:advice_json,:phrased_text,"
        ":status,:resolved_at,:origin,:created_at)",
        full,
    )
    conn.commit()
    return {"status": full["status"], "resolved_at": full["resolved_at"]}


def set_cached_advice(conn: sqlite3.Connection, household_id: str, payload_json: str) -> None:
    """Store the precomputed advice payload for a household (one row per tenant)."""
    from datetime import datetime, timezone

    conn.execute(
        "INSERT OR REPLACE INTO advice_cache (household_id, payload_json, computed_at) "
        "VALUES (?, ?, ?)",
        (household_id, payload_json, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_cached_advice(conn: sqlite3.Connection, household_id: str) -> str | None:
    """Return the stored advice payload JSON for a household, or None if not yet
    computed (caller falls back to computing on demand)."""
    row = conn.execute(
        "SELECT payload_json FROM advice_cache WHERE household_id = ?", (household_id,)
    ).fetchone()
    return row["payload_json"] if row is not None else None


def set_detected_insight_status(
    conn: sqlite3.Connection, household_id: str, fact_key: str, status: str
) -> dict | None:
    """Update a detected insight's workflow status and keep the cached advice
    payload in sync for fast UI refreshes."""
    from datetime import datetime, timezone

    rows = conn.execute(
        "SELECT id FROM insight_events WHERE origin='detected' AND household_id=? AND fact_key=?",
        (household_id, fact_key),
    ).fetchall()
    if not rows:
        return None

    resolved_at = datetime.now(timezone.utc).isoformat() if status == "resolved" else None
    conn.executemany(
        "UPDATE insight_events SET status=?, resolved_at=? WHERE id=?",
        [(status, resolved_at, row["id"]) for row in rows],
    )
    _update_cached_advice_status(conn, household_id, fact_key, status)
    conn.commit()
    return {"fact_key": fact_key, "status": status, "resolved_at": resolved_at}


def _update_cached_advice_status(
    conn: sqlite3.Connection, household_id: str, fact_key: str, status: str
) -> None:
    cached = get_cached_advice(conn, household_id)
    if cached is None:
        return
    items = json.loads(cached)
    updated = []
    for item in items:
        if item.get("fact_key") == fact_key:
            item = {**item, "status": status}
        if item.get("status") != "resolved":
            updated.append(item)
    conn.execute(
        "UPDATE advice_cache SET payload_json=? WHERE household_id=?",
        (json.dumps(updated), household_id),
    )


def add_applied_advice(conn: sqlite3.Connection, household_id: str, fact_key: str,
                       title: str | None, benefit_eur: float | None,
                       applied_at: str | None = None) -> None:
    """Record that a household applied a piece of advice and locked in its benefit."""
    from datetime import datetime, timezone

    conn.execute(
        "INSERT INTO applied_advice (household_id,fact_key,title,benefit_eur,applied_at) "
        "VALUES (?,?,?,?,?)",
        (household_id, fact_key, title, benefit_eur,
         applied_at or datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_applied_advice(conn: sqlite3.Connection, household_id: str) -> list[sqlite3.Row]:
    """Advice this household has already applied, newest first."""
    return conn.execute(
        "SELECT fact_key,title,benefit_eur,applied_at FROM applied_advice "
        "WHERE household_id=? ORDER BY applied_at DESC",
        (household_id,),
    ).fetchall()


def get_realized_savings(conn: sqlite3.Connection, household_id: str) -> float:
    """Total annual benefit the household has locked in from applied advice."""
    row = conn.execute(
        "SELECT COALESCE(SUM(benefit_eur),0) AS total FROM applied_advice WHERE household_id=?",
        (household_id,),
    ).fetchone()
    return float(row["total"] or 0.0)


def get_devices(conn: sqlite3.Connection, household_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM devices WHERE household_id=? ORDER BY id", (household_id,)
    ).fetchall()


def get_monthly_bills(conn: sqlite3.Connection, household_id: str) -> list[sqlite3.Row]:
    """A household's monthly bills, oldest first — the month-to-month series used
    by the dashboard's utilization visualizations."""
    return conn.execute(
        "SELECT month, total_bill_eur, consumption_kwh, grid_import_kwh "
        "FROM monthly_bills WHERE household_id=? ORDER BY month",
        (household_id,),
    ).fetchall()


def get_catalog(conn: sqlite3.Connection, category: str | None = None) -> list[sqlite3.Row]:
    if category:
        return conn.execute(
            "SELECT * FROM appliance_catalog WHERE category=? ORDER BY id", (category,)
        ).fetchall()
    return conn.execute("SELECT * FROM appliance_catalog ORDER BY category, id").fetchall()


def telemetry_time_range(conn: sqlite3.Connection, household_id: str) -> tuple[str, str] | None:
    """Min/max telemetry timestamp for a tenant — used to derive the data's
    timeframe rather than assuming a calendar year."""
    row = conn.execute(
        "SELECT MIN(ts) AS lo, MAX(ts) AS hi FROM telemetry WHERE household_id = ?",
        (household_id,),
    ).fetchone()
    if row is None or row["lo"] is None:
        return None
    return row["lo"], row["hi"]
