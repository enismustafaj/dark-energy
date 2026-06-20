"""The four concrete (mocked) actions the platform offers.

Each computes a real, data-grounded effect (e.g. the genuine cheapest price
window from ``dynamic_prices``) but applies it via the mock adapter. Guards 409
when the household lacks the relevant asset.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from ..models import ActionEffect
from .base import Action, ActionError, _household, register
from .adapters import DeviceAdapter


def _cheapest_hours(conn: sqlite3.Connection, tariff_id: str, n: int = 4) -> list[int]:
    """The n cheapest hours-of-day by recent retail price (spot + adder)."""
    rows = conn.execute(
        "SELECT ts, spot_price_eur_per_kwh FROM dynamic_prices ORDER BY ts DESC LIMIT 168"
    ).fetchall()
    if not rows:
        return list(range(n))
    s = pd.Series({pd.to_datetime(r["ts"]): r["spot_price_eur_per_kwh"] for r in rows})
    tariff = conn.execute(
        "SELECT spot_adder_eur_per_kwh FROM tariffs WHERE tariff_id=?", (tariff_id,)
    ).fetchone()
    adder = (tariff["spot_adder_eur_per_kwh"] or 0.0) if tariff else 0.0
    by_hour = (s + adder).groupby(s.index.hour).mean()
    return [int(h) for h in by_hour.nsmallest(n).index]


@register
class ScheduleEvCharge:
    type = "schedule_ev_charge"
    label = "Schedule EV charging"

    def validate(self, conn, household_id, params):
        h = _household(conn, household_id)
        if not h["ev_charger"]:
            raise ActionError("This household has no EV charger.")

    def execute(self, conn, household_id, params, adapter: DeviceAdapter) -> ActionEffect:
        h = _household(conn, household_id)
        hours = _cheapest_hours(conn, h["tariff_id"], n=3)
        target_soc = params.get("target_soc_pct", 80)
        window = f"{min(hours):02d}:00–{max(hours)+1:02d}:00"
        adapter.apply(household_id, {"action": self.type, "hours": hours, "target": target_soc})
        return ActionEffect(
            status="executed",
            message=(f"EV charging scheduled into the cheapest window "
                     f"({window}) to reach {target_soc}% — estimated to cut charging "
                     f"cost versus charging now."),
            schedule={"hours": hours, "target_soc_pct": target_soc},
            details={"window": window},
        )


@register
class ShiftHeatpump:
    type = "shift_heatpump_to_cheap_window"
    label = "Shift heat pump to cheap hours"

    def validate(self, conn, household_id, params):
        h = _household(conn, household_id)
        if not h["heat_pump"]:
            raise ActionError("This household has no heat pump.")

    def execute(self, conn, household_id, params, adapter: DeviceAdapter) -> ActionEffect:
        h = _household(conn, household_id)
        hours = _cheapest_hours(conn, h["tariff_id"], n=4)
        window = f"{min(hours):02d}:00–{max(hours)+1:02d}:00"
        adapter.apply(household_id, {"action": self.type, "hours": hours})
        return ActionEffect(
            status="executed",
            message=(f"Heat pump pre-heating shifted toward cheaper/sunnier hours "
                     f"({window})."),
            schedule={"hours": hours},
            details={"window": window},
        )


@register
class SetBatteryReserve:
    type = "set_battery_reserve"
    label = "Set battery reserve"

    def validate(self, conn, household_id, params):
        h = _household(conn, household_id)
        if not (h["battery_kwh"] and h["battery_kwh"] > 0):
            raise ActionError("This household has no battery storage.")

    def execute(self, conn, household_id, params, adapter: DeviceAdapter) -> ActionEffect:
        reserve = int(params.get("reserve_pct", 20))
        reserve = max(0, min(reserve, 100))
        adapter.apply(household_id, {"action": self.type, "reserve_pct": reserve})
        return ActionEffect(
            status="executed",
            message=f"Battery reserve floor set to {reserve}%.",
            details={"reserve_pct": reserve},
        )


@register
class BookMaintenance:
    type = "book_maintenance"
    label = "Book a maintenance visit"

    def validate(self, conn, household_id, params):
        _household(conn, household_id)  # always available

    def execute(self, conn, household_id, params, adapter: DeviceAdapter) -> ActionEffect:
        device = params.get("device", "heat pump")
        adapter.apply(household_id, {"action": self.type, "device": device})
        return ActionEffect(
            status="executed",
            message=(f"A maintenance visit for your {device} has been requested. "
                     f"A technician will contact you to schedule an inspection."),
            details={"device": device},
        )


@register
class SuggestTariffSwitch:
    type = "suggest_tariff_switch"
    label = "Compare tariffs"

    def validate(self, conn, household_id, params):
        _household(conn, household_id)  # always available

    def execute(self, conn, household_id, params, adapter: DeviceAdapter) -> ActionEffect:
        from ..analytics import frames

        h = _household(conn, household_id)
        df = frames.load_window(conn, household_id)
        if df.empty:
            raise ActionError("Not enough data to compare tariffs.")

        import_kwh = (df["grid_import_kw"].fillna(0) * 0.25)
        # Current actual energy cost over the full history.
        current_cost = float((import_kwh * df["price_eur_per_kwh"].fillna(0)).sum())

        # Cost under the *other* tariff type.
        other = conn.execute(
            "SELECT tariff_id,type,energy_rate_eur_per_kwh,spot_adder_eur_per_kwh "
            "FROM tariffs WHERE tariff_id != ?", (h["tariff_id"],)
        ).fetchone()
        if other is None:
            raise ActionError("No alternative tariff available.")

        if other["type"] == "fixed_rate":
            alt_cost = float((import_kwh * other["energy_rate_eur_per_kwh"]).sum())
        else:
            # Reprice each step's import at spot + the other tariff's adder.
            prices = pd.Series(
                {pd.to_datetime(r["ts"]): r["spot_price_eur_per_kwh"]
                 for r in conn.execute("SELECT ts,spot_price_eur_per_kwh FROM dynamic_prices")}
            )
            hourly = prices + (other["spot_adder_eur_per_kwh"] or 0.0)
            step_price = df.index.to_series().apply(
                lambda t: hourly.get(t.replace(minute=0), df["price_eur_per_kwh"].mean())
            )
            alt_cost = float((import_kwh.values * step_price.values).sum())

        delta = current_cost - alt_cost
        better = delta > 0
        msg = (
            f"Over your history, energy cost €{current_cost:,.0f} on your current "
            f"tariff vs €{alt_cost:,.0f} on the {other['tariff_id']} tariff — "
            + (f"switching could save about €{delta:,.0f}." if better
               else f"your current tariff is about €{-delta:,.0f} cheaper, so staying put is better.")
            + " Note: switching is subject to your contract notice period."
        )
        return ActionEffect(
            status="executed",
            message=msg,
            expected_savings_eur=round(delta, 0) if better else 0.0,
            details={"current_tariff": h["tariff_id"], "alternative": other["tariff_id"],
                     "current_cost_eur": round(current_cost, 0),
                     "alternative_cost_eur": round(alt_cost, 0)},
        )
