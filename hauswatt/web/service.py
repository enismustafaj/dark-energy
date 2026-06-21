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

from .. import actions  # noqa: F401 ensure builtin actions register
from .. import rules
from ..actions import builtin  # noqa: F401
from ..actions.base import ActionError, get_action
from ..ai.phraser import get_phraser
from ..ai.template_phraser import ACTION_LABELS
from ..analytics import anomalies as anomalies_mod
from ..analytics import facts as facts_mod
from ..analytics import status as status_mod
from ..db import (
    get_applied_advice,
    get_cached_advice,
    get_contract,
    get_devices,
    get_household,
    get_monthly_bills,
    get_realized_savings,
    set_cached_advice,
    upsert_detected_insight,
)

# Display metadata per device category for the star nodes. The 'household'
# device is the hub itself, not a point on the star, so it has no entry here.
NODE_META = {
    "pv": {"icon": "☀️", "label": "Solar PV"},
    "battery": {"icon": "🔋", "label": "Battery"},
    "heat_pump": {"icon": "♨️", "label": "Heat pump"},
    "ev": {"icon": "🚗", "label": "EV"},
    "contract": {"icon": "📄", "label": "Contract"},
}

# These are direct mocked device-control operations the agent can execute
# without waiting on a human/vendor workflow. Other recommendations remain
# manual tasks and are resolved by the user when completed.
AGENT_ACTIONABLE_KEYS = {
    "schedule_ev_charge",
    "shift_heatpump_to_cheap_window",
    "set_battery_reserve",
}


# Typical EV efficiency, kWh per km (~18 kWh/100 km), for translating battery
# capacity into a range a person can picture.
_EV_KWH_PER_KM = 0.18


def _node_metric(category: str, dev: sqlite3.Row | None, sq) -> str:
    """A plain-language, one-line takeaway for a device node.

    The point is what the device *does for the household*, not its spec sheet —
    no kWp / SCOP / kWh jargon. Every figure is grounded in the home's own data.
    """
    if category == "pv":
        # Share of the home's electricity met by its own solar (vs. the grid).
        if sq and sq.consumption_kwh:
            self_suff = max(0, (sq.consumption_kwh - sq.grid_import_kwh) / sq.consumption_kwh)
            return f"Covers ~{self_suff * 100:.0f}% of your power"
        return "Generates your own power"
    if category == "battery":
        # Roughly how long the stored energy could run the home at typical load.
        cap = dev["capacity_kwh"] or 0
        avg_load_kw = (sq.consumption_kwh / 8760) if sq and sq.consumption_kwh else 0
        if cap and avg_load_kw:
            hours = cap / avg_load_kw
            return f"Backs up ~{hours:.0f} hours of your home"
        return "Stores solar for later"
    if category == "heat_pump":
        # SCOP expressed as a multiplier anyone can grasp.
        scop = dev["efficiency"] or 0
        if scop:
            return f"~{scop:.0f}× the heat per € of power"
        return "Efficient electric heating"
    if category == "ev":
        cap = dev["capacity_kwh"] or 0
        if cap:
            km = round(cap / _EV_KWH_PER_KM / 10) * 10  # nearest 10 km
            return f"~{km:.0f} km on a full charge"
        return "Charges at home"
    return ""


def household_view(conn: sqlite3.Connection, household_id: str) -> dict | None:
    h = get_household(conn, household_id)
    if h is None:
        return None
    sq = status_mod.status_quo(conn, household_id)
    contract = get_contract(conn, household_id)
    devices = get_devices(conn, household_id)

    # --- nodes (star points) — the household device is the hub, not a point ---
    nodes = []
    for d in devices:
        if d["category"] == "household":
            continue
        meta = NODE_META.get(d["category"], {"icon": "⚙️", "label": d["category"]})
        nodes.append({
            "kind": "device", "device_id": d["id"], "category": d["category"],
            "icon": meta["icon"], "label": meta["label"],
            "metric": _node_metric(d["category"], d, sq) if sq else "",
        })
    if contract is not None:
        model = (contract["pricing_model"] or "").lower()
        tariff_desc = "Price changes through the day" if "dynamic" in model else "One fixed price"
        nodes.append({
            "kind": "contract", "device_id": None, "category": "contract",
            "icon": NODE_META["contract"]["icon"], "label": NODE_META["contract"]["label"],
            "metric": tariff_desc,
        })

    # Advice is precomputed off the request path (see recompute_advice, called on
    # ingest). Read the cached payload so GET /view does no rule-engine work. Only
    # if it has never been computed for this tenant do we compute once, lazily.
    cached = get_cached_advice(conn, household_id)
    advice = json.loads(cached) if cached is not None else recompute_advice(conn, household_id)

    # Advice the household has already acted on, and the annual benefit realized.
    applied = [dict(r) for r in get_applied_advice(conn, household_id)]
    realized_savings = round(get_realized_savings(conn, household_id))

    # Keep the two sets disjoint: an applied recommendation is no longer "open",
    # so drop it from the live advice list. This is the single source of truth —
    # `advice` = still-open, `applied_advice` = already realized, no overlap — so
    # every count the UI derives (open list, available savings, realized) agrees.
    applied_keys = {a["fact_key"] for a in applied}
    advice = [a for a in advice if a["fact_key"] not in applied_keys]

    return {
        "household": dict(h),
        "hub": sq.as_dict() if sq else None,
        "nodes": nodes,
        "advice": advice,
        "applied_advice": applied,
        "realized_savings_eur": realized_savings,
    }


def recompute_advice(conn: sqlite3.Connection, household_id: str) -> list[dict]:
    """Run the engine, phrase + persist results, and cache the ranked advice
    payload for fast reads. Call this off the request path (on ingest)."""
    advice = _ranked_advice(conn, household_id)
    set_cached_advice(conn, household_id, json.dumps(advice))
    return advice


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
        agent_actionable = _agent_actionable(conn, household_id, f.suggested_action_key)
        # Persist as a detected insight (carries category/device/benefit/advice).
        row = facts_mod.fact_to_event_row(f, phrased_text=body)
        row.update({
            "category": f.category, "device_id": f.device_id,
            "benefit_eur": r.benefit_eur or None,
            "advice_json": r.advice.model_dump_json() if r.advice else None,
        })
        persisted = upsert_detected_insight(conn, row)
        status = persisted["status"]

        item = {
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
            "agent_actionable": agent_actionable,
            "status": status,
            # The grounded figures behind the advice + an optional small dataset the
            # UI renders as a chart (see _advice_viz). Lets each card show "how this
            # works" with real supporting data, no extra API round-trip.
            "numbers": dict(f.numbers),
            "viz": _advice_viz(conn, household_id, r),
        }
        if status != "resolved":
            out.append(item)
    return out


def _advice_viz(conn: sqlite3.Connection, household_id: str, result) -> dict | None:
    """Build a compact, grounded visualization payload for an advice item, or None
    when the advice type has no chart. Every figure here already exists in the
    Fact's numbers, the Advice projection, or the household's stored data — nothing
    is invented.
    """
    f = result.fact
    nums = f.numbers
    adv = result.advice

    if f.key == "bill_spike":
        bills = get_monthly_bills(conn, household_id)
        if len(bills) < 2:
            return None
        return {
            "kind": "monthly_bills",
            "series": [{"month": b["month"], "total_eur": round(b["total_bill_eur"] or 0, 2)}
                       for b in bills],
            "high_month": nums.get("high_month"),
            "low_month": nums.get("low_month"),
        }

    if f.key == "cheapest_window":
        contract = get_contract(conn, household_id)
        tariff_id = contract["tariff_id"] if contract else "dynamic"
        by_hour = anomalies_mod.hourly_retail_prices(conn, tariff_id)
        if by_hour is None:
            return None
        return {
            "kind": "hourly_price",
            "by_hour": [{"hour": int(h), "price": round(float(p), 3)}
                        for h, p in by_hour.items()],
            "cheap_hour": nums.get("cheap_hour"),
        }

    if f.key == "high_baseload":
        baseload = float(nums.get("baseload_kw", 0) or 0)
        ratio_pct = float(nums.get("ratio_pct", 0) or 0)
        # ratio_pct = baseload / avg_load * 100  ->  avg_load = baseload / ratio
        avg_load = round(baseload / (ratio_pct / 100), 3) if ratio_pct else 0
        return {
            "kind": "baseload",
            "baseload_kw": round(baseload, 3),
            "avg_load_kw": avg_load,
        }

    if f.key in ("add_battery", "battery_upsize") and adv is not None:
        # Frame the grid-dependence reduction as before/after annual grid spend.
        if adv.baseline_cost_eur is None or adv.counterfactual_cost_eur is None:
            return None
        return {
            "kind": "grid_independence",
            "now_eur": round(adv.baseline_cost_eur, 0),
            "after_eur": round(adv.counterfactual_cost_eur, 0),
            "capacity_kwh": nums.get("capacity_kwh") or nums.get("new_kwh"),
        }

    if f.key == "tariff_fit" and adv is not None:
        cur = nums.get("current_eur", adv.baseline_cost_eur)
        alt = nums.get("alternative_eur", adv.counterfactual_cost_eur)
        if cur is None or alt is None:
            return None
        return {
            "kind": "tariff_compare",
            "current_eur": round(float(cur), 0),
            "alternative_eur": round(float(alt), 0),
        }

    return None


def _agent_actionable(conn: sqlite3.Connection, household_id: str, action_key: str | None) -> bool:
    if not action_key or action_key not in AGENT_ACTIONABLE_KEYS:
        return False
    action = get_action(action_key)
    if action is None:
        return False
    try:
        action.validate(conn, household_id, {})
    except ActionError:
        return False
    return True
