"""Device simulator — replays a household's stored telemetry as live device
readings POSTed to the ingest API.

Each step is emitted as per-device ``DeviceReading``s (only for the devices the
household actually has — e.g. HH-1004 emits only PV + household), so the live
path exercises the unified-schema merge rather than just re-posting whole rows.
Deterministic and controllable: an internal cursor walks the stored series at a
configurable speed; ``--clock`` chooses whether to keep the original timestamps,
rebase them to now, or continue past the end of the data.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import httpx

from ..config import get_settings
from ..db import connect, get_household

ALL_DEVICES = ["pv", "battery", "heatpump", "ev", "household"]

# Which telemetry columns each device reports.
DEVICE_METRICS = {
    "pv": ["pv_production_kw"],
    "battery": ["battery_charge_kw", "battery_discharge_kw", "battery_soc_kwh", "battery_soc_pct"],
    "heatpump": ["heatpump_kw"],
    "ev": ["ev_charging_kw"],
    "household": ["house_load_kw"],
}


def _devices_for(household: dict, requested: str) -> list[str]:
    """Resolve the device list, dropping devices the household lacks."""
    base = ALL_DEVICES if requested == "all" else [d.strip() for d in requested.split(",")]
    present = []
    for d in base:
        if d == "battery" and not (household["battery_kwh"] and household["battery_kwh"] > 0):
            continue
        if d == "heatpump" and not household["heat_pump"]:
            continue
        if d == "ev" and not household["ev_charger"]:
            continue
        if d == "pv" and not (household["pv_kwp"] and household["pv_kwp"] > 0):
            continue
        present.append(d)
    return present


def run_sim(
    household_id: str, devices: str, speed: float, clock: str, seed: int,
    base_url: str, limit: int | None,
) -> int:
    conn = connect()
    h = get_household(conn, household_id)
    if h is None:
        print(f"Unknown household {household_id}")
        return 1
    h = dict(h)
    device_list = _devices_for(h, devices)
    print(f"Simulating {household_id} devices={device_list} clock={clock} speed={speed}/s")

    rows = conn.execute(
        "SELECT * FROM telemetry WHERE household_id=? ORDER BY ts", (household_id,)
    ).fetchall()
    conn.close()
    if not rows:
        print("No telemetry to replay — run `energyintelligence seed` first.")
        return 1

    now = datetime.utcnow().replace(second=0, microsecond=0)
    # Align rebased clock to a 15-min grid.
    now = now.replace(minute=(now.minute // 15) * 15)
    sent = 0
    delay = 1.0 / speed if speed > 0 else 0.0

    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        for i, row in enumerate(rows):
            if limit is not None and sent >= limit:
                break
            ts = _stamp(row["ts"], clock, now, i)
            for device in device_list:
                metrics = {c: row[c] for c in DEVICE_METRICS[device] if row[c] is not None}
                payload = {
                    "household_id": household_id,
                    "device_type": device,
                    "ts": ts,
                    "metrics": metrics,
                    "outdoor_temp_c": row["outdoor_temp_c"],
                    "price_eur_per_kwh": row["price_eur_per_kwh"],
                }
                try:
                    client.post("/api/ingest/reading", json=payload)
                except httpx.HTTPError as e:
                    print(f"  POST failed: {e}")
                    return 1
            sent += 1
            if sent % 50 == 0:
                print(f"  sent {sent} steps (latest ts {ts})")
            if delay:
                time.sleep(delay)
    print(f"Done — sent {sent} steps.")
    return 0


def _stamp(original_ts: str, clock: str, now: datetime, i: int) -> str:
    if clock == "original":
        return original_ts
    # rebase / continue: lay steps onto a 15-min grid ending at "now".
    return (now + timedelta(minutes=15 * i)).isoformat()
