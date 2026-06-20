"""Dashboard service layer: assemble the per-household star-diagram view.

Tenant-scoped throughout. Runs the rule engine, phrases each rule's Fact, and
shapes the result into:
  * hub      — the status-quo snapshot (centre of the star)
  * nodes    — the household's devices + a contract node (the star's points)
  * advice   — ranked RuleResults, phrased, each tagged with its node so the UI
               can filter to a device when its node is clicked.
"""

from __future__ import annotations

import json
import sqlite3

from .. import rules
from ..ai.phraser import get_phraser
from ..ai.template_phraser import ACTION_LABELS
from ..analytics import facts as facts_mod
from ..analytics import status as status_mod
from ..db import get_contract, get_devices, get_household, upsert_detected_insight

# Display metadata per device category for the star nodes.
NODE_META = {
    "household": {"icon": "🏠", "label": "Household"},
    "pv": {"icon": "☀️", "label": "Solar PV"},
    "battery": {"icon": "🔋", "label": "Battery"},
    "heat_pump": {"icon": "♨️", "label": "Heat pump"},
    "ev": {"icon": "🚗", "label": "EV"},
    "ev_charger": {"icon": "🔌", "label": "Charger"},
    "contract": {"icon": "📄", "label": "Contract"},
}


def _node_metric(category: str, dev: sqlite3.Row | None, sq) -> str:
    """A one-line headline metric for a device node."""
    if category == "household":
        return f"{sq.consumption_kwh:.0f} kWh/yr"
    if category == "pv":
        return f"{(dev['rated_kw'] or 0):.1f} kWp · {sq.pv_production_kwh:.0f} kWh/yr"
    if category == "battery":
        return f"{(dev['capacity_kwh'] or 0):.0f} kWh"
    if category == "heat_pump":
        return f"SCOP {(dev['efficiency'] or 0):.1f}"
    if category == "ev":
        return f"{(dev['capacity_kwh'] or 0):.0f} kWh pack"
    if category == "ev_charger":
        return f"{(dev['rated_kw'] or 0):.0f} kW"
    return ""


def household_view(conn: sqlite3.Connection, household_id: str) -> dict | None:
    h = get_household(conn, household_id)
    if h is None:
        return None
    sq = status_mod.status_quo(conn, household_id)
    contract = get_contract(conn, household_id)
    devices = get_devices(conn, household_id)

    # --- nodes (star points) ---
    nodes = []
    for d in devices:
        meta = NODE_META.get(d["category"], {"icon": "⚙️", "label": d["category"]})
        nodes.append({
            "kind": "device", "device_id": d["id"], "category": d["category"],
            "icon": meta["icon"], "label": meta["label"],
            "metric": _node_metric(d["category"], d, sq) if sq else "",
        })
    if contract is not None:
        nodes.append({
            "kind": "contract", "device_id": None, "category": "contract",
            "icon": NODE_META["contract"]["icon"], "label": NODE_META["contract"]["label"],
            "metric": f"{contract['tariff_id']} · €{contract['base_fee_eur_per_month']:.0f}/mo",
        })

    advice = _ranked_advice(conn, household_id)

    return {
        "household": dict(h),
        "hub": sq.as_dict() if sq else None,
        "nodes": nodes,
        "advice": advice,
    }


def _ranked_advice(conn: sqlite3.Connection, household_id: str) -> list[dict]:
    """Run the engine, phrase each result, persist it, return ranked advice dicts."""
    results = rules.run_rules(conn, household_id)
    if not results:
        return []

    bundle = facts_mod.build_bundle(conn, household_id, [r.fact for r in results])
    phrased = {p.fact_key: p for p in get_phraser().phrase(bundle)}

    out = []
    for r in results:
        f = r.fact
        pi = phrased.get(f.key)
        title = pi.title if pi else f.title
        body = pi.body if pi else f.detail
        action_label = (pi.action_label if pi and pi.action_label
                        else ACTION_LABELS.get(f.suggested_action_key or ""))
        # Persist as a detected insight (carries category/device/benefit/advice).
        row = facts_mod.fact_to_event_row(f, phrased_text=body)
        row.update({
            "category": f.category, "device_id": f.device_id,
            "benefit_eur": r.benefit_eur or None,
            "advice_json": r.advice.model_dump_json() if r.advice else None,
        })
        upsert_detected_insight(conn, row)

        out.append({
            "fact_key": f.key,
            "category": f.category,
            "device_id": f.device_id,
            "severity": f.severity,
            "title": title,
            "body": body,
            "benefit_eur": round(r.benefit_eur) if r.benefit_eur else None,
            "advice": r.advice.model_dump() if r.advice else None,
            "action_type": f.suggested_action_key,
            "action_label": action_label,
        })
    return out
