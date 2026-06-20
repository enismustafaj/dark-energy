"""FastAPI application: dashboard, ingest API, actions API, and SSE stream.

Tenant separation runs through the whole surface — every route takes a
``household_id`` (path or body), validates it, and only ever touches that
tenant's data and event-bus channel.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import actions  # noqa: F401 ensure builtin actions register
from ..actions import builtin  # noqa: F401
from ..actions.adapters import MockDeviceAdapter
from ..actions.base import ActionError, all_actions, get_action
from ..config import get_settings
from ..db import connect, household_exists, household_ids, init_db
from ..events.bus import Event, bus
from ..ingest.mapping import reading_to_record, merge_into_record
from ..models import DeviceReading, TelemetryRecord
from .service import household_view, _ranked_advice

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

app = FastAPI(title="Dark Energy")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

_adapter = MockDeviceAdapter()


def db() -> sqlite3.Connection:
    conn = connect()
    init_db(conn)
    return conn


def _require_household(conn: sqlite3.Connection, household_id: str) -> None:
    if not household_exists(conn, household_id):
        raise HTTPException(status_code=404, detail=f"Unknown household {household_id}")


# --- dashboard -------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = db()
    try:
        homes = [dict(conn.execute(
            "SELECT household_id,name,city,tariff_id FROM households WHERE household_id=?",
            (hid,)).fetchone()) for hid in household_ids(conn)]
    finally:
        conn.close()
    return templates.TemplateResponse(request, "index.html", {"homes": homes})


@app.get("/h/{household_id}", response_class=HTMLResponse)
def dashboard(request: Request, household_id: str):
    conn = db()
    try:
        _require_household(conn, household_id)
        view = household_view(conn, household_id)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "dashboard.html", {"v": view, "hid": household_id}
    )


@app.get("/api/advice/{household_id}")
def advice(household_id: str, device_id: int | None = None, category: str | None = None,
           limit: int = 5):
    """Ranked advice for a household, optionally filtered to one device node or
    the contract node. Default returns the top `limit` by benefit."""
    conn = db()
    try:
        _require_household(conn, household_id)
        items = _ranked_advice(conn, household_id)
    finally:
        conn.close()
    if category is not None:
        items = [a for a in items if a["category"] == category]
    if device_id is not None:
        items = [a for a in items if a["device_id"] == device_id]
    elif category is None:
        # Unfiltered default view → top N across everything.
        items = items[:limit]
    return {"household_id": household_id, "advice": items}


# --- ingest ----------------------------------------------------------------

async def _accept_record(rec: TelemetryRecord) -> dict:
    conn = db()
    try:
        _require_household(conn, rec.household_id)
        from ..db import upsert_telemetry
        upsert_telemetry(conn, rec)
    finally:
        conn.close()
    residual = rec.balance_residual()
    if residual > get_settings().balance_epsilon_kw:
        # Accept but flag — live devices drift; do not reject.
        pass
    await bus.publish(rec.household_id, Event(type="telemetry", data={
        "ts": rec.ts.isoformat(),
        "pv_production_kw": round(rec.pv_production_kw, 3),
        "total_consumption_kw": round(rec.total_consumption_kw, 3),
        "grid_import_kw": round(rec.grid_import_kw, 3),
        "grid_export_kw": round(rec.grid_export_kw, 3),
        "battery_soc_pct": round(rec.battery_soc_pct, 1),
        "price_eur_per_kwh": rec.price_eur_per_kwh,
        "outdoor_temp_c": rec.outdoor_temp_c,
    }))
    return {"accepted": True, "household_id": rec.household_id,
            "ts": rec.ts.isoformat(), "balance_residual": round(residual, 4)}


@app.post("/api/ingest/snapshot")
async def ingest_snapshot(record: TelemetryRecord):
    return await _accept_record(record)


@app.post("/api/ingest/reading")
async def ingest_reading(reading: DeviceReading):
    conn = db()
    try:
        _require_household(conn, reading.household_id)
        # Merge the device slice onto the latest known record for continuity.
        prev = conn.execute(
            "SELECT * FROM telemetry WHERE household_id=? ORDER BY ts DESC LIMIT 1",
            (reading.household_id,),
        ).fetchone()
    finally:
        conn.close()
    rec = reading_to_record(reading, prev)
    return await _accept_record(rec)


@app.post("/api/ingest/batch")
async def ingest_batch(records: list[TelemetryRecord]):
    results = [await _accept_record(r) for r in records]
    return {"accepted": len(results)}


# --- actions ---------------------------------------------------------------

@app.get("/api/actions")
def list_actions(household_id: str):
    conn = db()
    try:
        _require_household(conn, household_id)
        rows = conn.execute(
            "SELECT id,action_type,status,effect_json,created_at FROM actions "
            "WHERE household_id=? ORDER BY created_at DESC LIMIT 50",
            (household_id,),
        ).fetchall()
    finally:
        conn.close()
    return {"actions": [dict(r) for r in rows]}


@app.post("/api/actions/{action_type}")
async def run_action(action_type: str, household_id: str, params: dict | None = None):
    params = params or {}
    action = get_action(action_type)
    if action is None:
        raise HTTPException(status_code=404, detail=f"Unknown action {action_type}")
    conn = db()
    try:
        _require_household(conn, household_id)
        try:
            action.validate(conn, household_id, params)
        except ActionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        try:
            effect = action.execute(conn, household_id, params, _adapter)
        except ActionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        conn.execute(
            "INSERT INTO actions (household_id,action_type,params_json,status,effect_json,"
            "created_at,executed_at) VALUES (?,?,?,?,?,?,?)",
            (household_id, action_type, json.dumps(params), effect.status,
             effect.model_dump_json(), datetime.utcnow().isoformat(),
             datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    await bus.publish(household_id, Event(type="action", data={
        "action_type": action_type, "label": action.label,
        "message": effect.message, "status": effect.status,
        "expected_savings_eur": effect.expected_savings_eur,
    }))
    return effect.model_dump()


# --- SSE -------------------------------------------------------------------

@app.get("/api/stream/{household_id}")
async def stream(household_id: str, request: Request):
    conn = db()
    try:
        _require_household(conn, household_id)
    finally:
        conn.close()

    async def gen():
        q = bus.subscribe(household_id)
        try:
            # Initial comment to open the stream.
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event: Event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"  # keep proxies open
                    continue
                yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"
        finally:
            bus.unsubscribe(household_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")
