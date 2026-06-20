# Dark Energy — Capability Spec

> A living spec for the customer **intelligence layer** over residential energy
> telemetry. Reflects what exists today (the thin slice) and is structured so
> you can iterate: each capability lists what's **built**, where it lives, and
> the **next iterations** worth taking.
>
> Tagline / north star: **Less cost. More loyalty. Zero disruption.**
> Status legend: ✅ built · 🟡 partial / stubbed · ⬜ not yet

---

## 0. Iteration 2 — Rule engine, devices, advice (current)

The intelligence core is now a **rule engine** over ETL metrics. Insights are
produced by a ruleset grouped into four categories; each rule is specific to a
part of the device set, has its own evaluation logic, and may attach an
**advice** whose cost is **re-evaluated by counterfactual replay** over the
household's real telemetry. Advice is ranked by annual customer cost benefit.

- **Devices table** (`devices`) is the canonical record of what a household owns
  (seeded from the richer contract asset specs), replacing `assets_json`.
- **Qualified-appliance catalog** (`enpal-track-dataset/qualified_appliances.json`
  → `appliance_catalog`) backs device-choice advice (efficiency/capacity/capex).
- **Counterfactual cost engine** (`analytics/costing.py` `replay_cost`) re-prices
  the year's real grid imports/exports under a rule's modified assumption.
- **Star-diagram dashboard**: household hub at centre, device + contract nodes
  radially; top-5 advice by benefit by default; click a node to filter advice to
  that device (or the contract). `GET /api/advice/{hh}?device_id=&category=`.

**Rule categories & rules** (`darkenergy/rules/`):
| Category | Rules | Counterfactual |
|---|---|---|
| `contract` | `tariff_fit` | reprice year under each alternative tariff |
| `device_choice` | `heatpump_upgrade`, `add_battery`, `battery_upsize` | SCOP-scaled load; battery dispatch (neutralizing the recorded battery for upsize) |
| `utilization` | `battery_grid_support`, `ev_v2h`, `cheapest_window` | price-aware battery/EV dispatch vs recorded |
| `fault` | `heatpump_overconsumption` (→ maintenance), `high_baseload`, `bill_spike` | none (ranked by severity) |

The four anomaly detectors from iteration 1 are **re-homed** as `fault` rules
(`rules/fault.py` wraps the still-tested `analytics/anomalies.py` functions).
`anomalies.detect_all` is superseded by `rules.run_rules`. Test scenarios in
`tests/test_rules.py` are the iteration surface — benefits asserted
directionally (sign/ranking/household), not exact euros.

**Honest modeling note:** the bundled homes already operate their batteries
near-optimally, so `battery_upsize` / `battery_grid_support` correctly **do not
fire** (no positive marginal benefit) — the engine reflects reality rather than
inventing savings. `add_battery` fires for the PV-only home (HH-1004) where the
counterfactual is clean. Heat-pump payback is long on this synthetic data
(€256/yr vs €14.5k capex) — the rule picks the **best-payback** unit and surfaces
the payback honestly. Both are good iteration targets (richer dispatch model;
realistic capex/savings).

**Next iterations (engine):**
- ⬜ Add genuinely-undersized / idle-battery test households so upsize/grid-support fire.
- ⬜ Confidence/uncertainty on advice benefit; multi-year payback with degradation.
- ⬜ Per-rule explanations surfaced through the AI phraser (currently phrases the Fact).
- ⬜ Action outcome tracking → did applying the advice realize the modeled benefit.

---

## 1. Product summary

Dark Energy turns per-device energy data (PV, battery, heat pump, EV, household
load) into **forecasts, anomaly detection, and AI-phrased, actionable advice**,
delivered to a per-household customer dashboard with live updates and one-tap
actions. The platform is the technical starting point — built as a complete but
minimal end-to-end slice across all four capability areas.

**Who it's for:** an energy provider's residential customers (the dashboard) and
the provider itself (the intelligence + retention engine behind it).

**How it pays off the tagline:**
- *Less cost* — forecasts, anomaly alerts, and cheap-window nudges/actions cut bills.
- *More loyalty* — proactive, plain-language guidance + tariff intelligence keeps customers.
- *Zero disruption* — actions are opt-in and reversible; devices are observed, not seized.

---

## 2. Architecture at a glance

```
Device simulators ──POST /api/ingest──┐
  (5 device types, local/mocked)      │
                                       ▼
Seed loader (dataset) ──────────►  SQLite (unified telemetry, per-tenant)
                                       │
                                       ▼
                            ETL / metrics  ─►  Forecast + Anomalies
                                       │              │
                                       │              ▼
                                       │      Facts (structured, grounded)
                                       │              │
                                       │              ▼
                                       │      AI phraser (template | Claude | OpenAI)
                                       ▼              │
                            Event bus (per-tenant) ◄──┘
                                       │
                  Dashboard ◄──SSE─────┘   Actions API (mocked effects)
```

**Two load-bearing invariants:**
1. **One unified telemetry schema** (`TelemetryRecord`) is written by *both* the
   seed loader and the live ingest endpoint → analytics is source-agnostic.
2. **The LLM only rephrases pre-computed numbers; it never invents them** →
   enforced by a post-generation grounding check with template fallback.

**Stack:** Python · FastAPI · Pydantic · pandas/numpy · SQLite (WAL) · Jinja2 +
vanilla JS + SSE · Anthropic / OpenAI SDKs (optional).

---

## 3. Capabilities

### 3.1 Real-time streaming for devices ✅
**Built:** Device simulators replay a household's stored telemetry as per-device
readings POSTed to an ingest API. 5 device types (PV, battery/wallbox, heat
pump, EV, household). Per-device payloads are merged into the unified record;
grid import/export is recomputed from the energy-balance equation. Configurable
clock (`original` / `rebase` / `continue`), speed, determinism. Live fan-out to
the dashboard via a per-tenant SSE event bus.
**Lives in:** `simulators/cli.py`, `ingest/{router-in-web-app,mapping.py}`, `events/bus.py`.
**Next iterations:**
- ⬜ Real device-integration adapter behind the ingest API (vendor webhooks / MQTT).
- ⬜ Authenticated, per-tenant ingest (API key / device token) replacing the body `household_id`.
- 🟡 Energy-balance check is "accept-and-flag"; surface flagged steps as a data-quality signal.
- ⬜ Backfill/replay endpoint with `Last-Event-ID` SSE resume (server replays missed steps from DB).
- ⬜ Out-of-order / late-arriving step handling beyond the current floor-to-grid upsert.

### 3.2 ETL over usage & production ✅
**Built:** Pure functions over a tenant+range telemetry DataFrame: energy totals
(kWh), cost (using the embedded per-step retail price), self-sufficiency %, PV
self-consumption %, per-device breakdown, night baseload/standby, period-over-
period delta, latest-status snapshot. Validated to reproduce the ground-truth
monthly bills **exactly**.
**Lives in:** `analytics/frames.py`, `analytics/metrics.py`.
**Next iterations:**
- ⬜ Materialized daily/monthly rollups (currently recomputed on demand — fine at this scale).
- ⬜ Carbon / grid-intensity metrics alongside cost.
- ⬜ Cohort/benchmark metrics ("vs similar homes").
- ⬜ Data-quality / gap detection as a first-class metric.

### 3.3 Forecasting ✅ (simple) / 🟡 (depth)
**Built:** Current-month bill forecast (run-rate + heating-degree-day framing,
with a plain-English `explanation`); short-horizon usage forecast (hour-of-week
trailing average). Explainable, no training pipeline.
**Lives in:** `analytics/forecast.py`.
**Next iterations:**
- 🟡 Bill forecast assumes remaining days resemble elapsed days — plug in an actual
  weather/temperature forecast feed for the heating component (the hook exists).
- ⬜ PV-production forecast from a clear-sky model × weather.
- ⬜ Confidence intervals on the forecast, not just a point estimate.
- ⬜ Validate/backtest forecasts against realized bills and report error.

### 3.4 Anomaly detection ✅
**Built:** Four rule-based detectors, each emitting a structured `Fact`, deduped
+ persisted: heat-pump overconsumption (temperature-conditioned trailing
baseline), high standby/baseload, bill-spike month, cheapest-charging-window
nudge. Reproduce the seeded benchmark events; thresholds derive from each home's
own distribution (no hardcoded dates). Skips detectors for absent assets.
**Lives in:** `analytics/anomalies.py`.
**Known limitation:** heat-pump faults are **localized** to the right window but
the reported excess-% is conservative (~12–24%) vs the dataset's headline ~60%,
because the synthetic faults are subtle on a low baseline.
**Next iterations:**
- 🟡 Sharpen the heat-pump magnitude estimate (cleaner fault-free baseline / changepoint detection).
- ⬜ More detectors: phantom/standby growth trend, PV underperformance vs expected, battery cycling inefficiency.
- ⬜ Severity scoring + suppression of repeat/known anomalies.
- ⬜ Run detection on a schedule / on-ingest (debounced) rather than on dashboard load.

### 3.5 AI layer — insights → user-friendly feedback ✅
**Built:** A `FactBundle` (computed numbers + context) is handed to a pluggable
`Phraser`. Default `TemplatePhraser` (deterministic, no key). `ClaudePhraser`
and `OpenAIPhraser` are interchangeable drop-ins behind one protocol, sharing a
base that does prompt-building, JSON parsing, and a **number-grounding
post-check** — any figure not in the source facts is rejected and the template
phrasing is used instead. Falls back to template if a key/SDK is missing.
**Lives in:** `ai/` (`phraser.py`, `template_phraser.py`, `llm_phraser.py`,
`claude_phraser.py`, `openai_phraser.py`, `prompts.py`).
**Next iterations:**
- ⬜ Conversational layer: "why is my bill high this month?" grounded on the same Facts + contracts.
- ⬜ Per-customer tone/length preferences; localization (the data is German households).
- ⬜ Cache phrasings keyed by Fact hash (table column exists; not yet used to skip re-calls).
- ⬜ Tariff/contract NLP over the free-text `contract_terms_text` field (notice periods, auto-renew).

### 3.6 Actions ✅ (mocked, extensible)
**Built:** Four real endpoints with mocked internal effects behind a clean
`Action` protocol (validate → execute → `ActionEffect`), persisted and published
to the live stream: schedule EV charge (picks the genuine cheapest window),
shift heat pump to cheap hours, set battery reserve, suggest tariff switch
(recomputes annual cost under the other tariff). 409s when the asset is absent.
A `MockDeviceAdapter` is swappable for a real `DeviceAdapter`.
**Lives in:** `actions/` (`base.py`, `builtin.py`, `adapters.py`).
**Next iterations:**
- ⬜ Real `DeviceAdapter` (REST to inverter/wallbox/thermostat) — drop-in via `apply(command)`.
- ⬜ Action outcome tracking: did the scheduled action actually save money? close the loop.
- ⬜ Scheduling/automation rules ("always charge EV in the cheapest window") not just one-shot actions.
- ⬜ Confirmation / undo UX for state-changing actions (the "zero disruption" promise).

### 3.7 Dashboard ✅ (simple)
**Built:** Server-rendered tenant picker → per-household dashboard. Cards: live
status (SSE-updated), cost-so-far + forecast with explanation, energy mix /
metrics, insights feed with per-insight action buttons, action log. Cards
auto-hide for absent devices. Live updates over SSE (telemetry, insight, action
events) with heartbeats.
**Lives in:** `web/` (`app.py`, `service.py`, `templates/`, `static/`).
**Next iterations:**
- ⬜ Charts (consumption vs production over the day, cost trend) — currently numeric cards only.
- ⬜ Historical views / date-range picker (data spans a full year; UI shows current month).
- ⬜ Richer SPA if interactivity grows (deliberately deferred — "keep it simple").
- ⬜ SSE reconnection with replay-from-DB (currently reconnect-from-now).

---

## 4. Cross-cutting

### Multi-tenant separation ✅
`household_id` is the tenant key and the leading column of every fact-table
index; all reads funnel through `frames.load_window(hh, …)` / scoped helpers —
there is no cross-tenant query path. The SSE event bus is keyed per household.
**Lives in:** `db.py`, `events/bus.py`, every `web/app.py` route.
**Next:** ⬜ Real auth (session / API key → tenant resolution); the seam is there,
the param is currently trusted.

### Format-driven, not dataset-specific ✅
The bundled dataset is treated as *one instance of a format*. Household IDs, the
year, and the time range are all **derived from the loaded data** — verified by
seeding a re-dated (2023) copy and confirming detectors fire with 2023 periods.
Point `DARKENERGY_DATASET_DIR` at any same-format dataset.
**Next:** ⬜ A schema/validator for the dataset format so new scenarios fail loudly.

### Configuration ✅
All settings are env vars prefixed `DARKENERGY_` (`config.py`): `DB_PATH`,
`DATASET_DIR`, `PHRASER_BACKEND`, `CLAUDE_MODEL`, `OPENAI_MODEL`, `HOST`, `PORT`,
`BALANCE_EPSILON_KW`.

### Tests ✅
`pytest` (18 passing): metrics reproduce `monthly_bills.json`; detectors
reproduce `insight_events.json`; ingest merge holds the energy-balance invariant;
phraser never surfaces an ungrounded number; HH-1004 degrades gracefully.
**Next:** ⬜ API/route tests via TestClient; ⬜ SSE integration test in CI.

---

## 5. Data model (the contract)

**`TelemetryRecord`** (unified, per 15-min step; kW averaged, ×0.25 → kWh):
`household_id, ts, outdoor_temp_c, pv_production_kw, house_load_kw, heatpump_kw,
ev_charging_kw, total_consumption_kw, battery_charge_kw, battery_discharge_kw,
battery_soc_kwh, battery_soc_pct, grid_import_kw, grid_export_kw,
price_eur_per_kwh, source`.
**Invariant:** `pv + grid_import + battery_discharge = total_consumption + grid_export + battery_charge`.

**`DeviceReading`** (per-device slice → merged into the above):
`household_id, device_type ∈ {pv,battery,heatpump,ev,household}, ts, metrics{...},
outdoor_temp_c?, price_eur_per_kwh?`.

**`Fact` / `FactBundle`** (the AI grounding contract):
`Fact{key, household_id, type, severity, period, numbers{...}, template_id,
suggested_action_key}` — the phraser may only reuse values in `numbers`.

**Reference tables:** `households`, `contracts`, `tariffs`, `dynamic_prices`,
`monthly_bills`, `insight_events`, `forecasts`, `actions`.

---

## 6. Surfaces (API + CLI)

**CLI:** `darkenergy seed` · `darkenergy serve` · `darkenergy sim --household … [--devices --speed --clock --seed --base-url --limit]`

**HTTP:**
| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Tenant picker |
| GET | `/h/{household_id}` | Dashboard |
| POST | `/api/ingest/snapshot` | One full `TelemetryRecord` |
| POST | `/api/ingest/reading` | One `DeviceReading` (merged) |
| POST | `/api/ingest/batch` | List of records |
| GET | `/api/actions?household_id=` | Action history |
| POST | `/api/actions/{type}?household_id=` | Run an action |
| GET | `/api/stream/{household_id}` | SSE (telemetry · insight · action) |

---

## 7. Suggested iteration tracks (pick a thread)

1. **Close the value loop** — action outcome tracking + automation rules + savings attribution. Most directly serves "less cost / more loyalty."
2. **Conversational intelligence** — a grounded Q&A layer over Facts + contracts + the `contract_terms_text` NLP. Highest-visibility AI surface.
3. **Forecasting depth** — weather-fed bill forecast, PV forecast, confidence intervals, backtesting.
4. **Production-readiness** — auth + per-tenant ingest tokens, a real device adapter, SSE replay, charts.
5. **Detection breadth** — more anomaly detectors + severity/suppression + on-ingest scheduling.

Each track is mostly independent and maps to one capability section above.
