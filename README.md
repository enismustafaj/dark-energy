# EnergyIntelligence

A customer **intelligence layer** over residential energy telemetry.
*Less cost. More loyalty. Zero disruption.*

Per-device energy data (PV, battery, heat pump, EV, household load) becomes
**forecasts, anomaly detection, and AI-phrased, actionable advice**, served over
an API to a per-household React dashboard with live SSE updates.

One unified telemetry schema is written by both the seed loader and the live
ingest endpoint, so analytics never knows whether a row is historical or
streamed. Every figure is computed deterministically; the LLM only rephrases a
structured fact bundle and a grounding check rejects any number it invents.

## Tech

| Layer | Stack |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 |
| Analytics | pandas, numpy |
| Storage | SQLite (`data.db`, seeded from the bundled dataset) |
| Streaming | Server-Sent Events (`sse-starlette`) |
| AI phrasing | pluggable — deterministic templates (default), or Claude / OpenAI |
| Frontend | React 19 + TypeScript, Vite, Mantine, Recharts |

## Run it

Prerequisites: Python 3.11+, Node 18+. From the repo root:

```bash
# 1. Backend deps (add ".[claude]" / ".[openai]" for LLM phrasing)
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 2. Frontend deps
(cd frontend && npm install)

# 3. Seed the dataset into data.db (~210k telemetry rows, 6 households)
.venv/bin/energyintelligence seed

# 4. Backend API on :8000  (hot-reload on; --no-reload to disable)
.venv/bin/energyintelligence serve --port 8000

# 5. Frontend dev server on :5173 (in a second terminal)
(cd frontend && npm run dev)
```

Open <http://localhost:5173> and pick a household. The Vite dev server proxies
`/api` to `http://127.0.0.1:8000`, so no CORS or extra config is needed.

```bash
.venv/bin/python -m pytest      # tests
(cd frontend && npm run build)  # production bundle in frontend/dist
```

## AI agent

The dashboard chat is backed by an LLM agent (`ai/chat_agent.py`) that explains
recommendations and takes control actions on the user's behalf. It calls the
OpenAI Responses API and is enabled by a single environment variable:

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | Enables the agent. **Required** for live replies. | — |

```bash
OPENAI_API_KEY=sk-... .venv/bin/energyintelligence serve --port 8000
```

Without `OPENAI_API_KEY` (or if the API call fails) the agent degrades to a
deterministic fallback that lists the household's available actions — the
dashboard stays usable, it just won't converse. The actions themselves are always
validated and executed by the backend, so the agent only decides *which* action
to invoke; it never fabricates an outcome.

## Architecture

```
simulators/  ─POST→  web/  ──→  db.py (SQLite)  ──→  analytics/  ──→  rules/  ──→  ai/  ──→  web/  ─SSE/JSON→  frontend/
device sim          ingest      one telemetry        ETL: metrics,    ranked      phrase a    API +
+ seed loader        API        schema for both      forecast,        advice +    FactBundle  SSE bus
                                live & historical    anomalies        actions     (grounded)
```

- **`models.py`** — the one `TelemetryRecord` schema + the device-category map.
- **`analytics/`** — pure, well-tested ETL: `metrics`, `forecast`, `anomalies`,
  `costing` (counterfactual cost replays), `status`.
- **`rules/`** — the advice engine. Each rule is scoped to a device category,
  gates itself with `applies()`, and returns a grounded `Fact` + optional
  `Advice` whose `benefit_eur` comes from a cost replay. Ranked by benefit.
- **`actions/`** — mocked-but-real control actions with a clean
  `validate` / `execute` split over a `DeviceAdapter`.
- **`ai/`** — the `Phraser` protocol (template / Claude / OpenAI) + a chat agent;
  rephrases facts only, with a number-grounding post-check.
- **`web/`** — FastAPI app: `/api/households`, `/api/households/{id}/view`,
  `/api/advice/{id}`, `/api/actions`, `/api/chat/{id}`, ingest, and the
  `/api/stream/{id}` SSE bus (keyed per household for tenant separation).

## Extending it

The engine is built around two registries, so the common extensions are small,
local, additive changes — no wiring elsewhere.

**Add a rule (insight / advice).** Drop a class into `energyintelligence/rules/`,
decorate with `@register`, and implement the `Rule` protocol. It's picked up
automatically and ranked with the rest by customer benefit. A misbehaving rule
is skipped, never breaks the engine.

```python
from .base import RuleContext, register
from ..models import RuleResult, Advice

@register
class BatteryUnderutilized:
    key = "battery_underutilized"
    category = "utilization"          # fault | contract | device_choice | utilization
    device_category = "battery"

    def applies(self, ctx: RuleContext) -> bool:
        return ctx.has("battery")     # auto-skips homes without a battery

    def evaluate(self, ctx: RuleContext) -> RuleResult | None:
        cf = ctx.replay(...)          # counterfactual cost replay → benefit_eur
        ...                           # return RuleResult(fact=..., advice=Advice(...))
```

The `RuleContext` hands you the telemetry DataFrame, devices, contract, catalog,
baseline cost, and helpers (`replay`, `annualize`, `has`, `catalog_for`). Then
register the new class in `rules/__init__.py`.

**Add a device category.** Extend the `DeviceType` literal and the
device-category column map in `models.py`, teach the seed loader / simulator to
emit its telemetry columns, and (optionally) write rules and actions scoped to
it via `device_category` / `validate`. Analytics is source-agnostic, so it needs
no changes. Homes lacking the device simply have their rules `applies()` return
`False` and their cards hidden — graceful degradation is built in.

**Add an action.** Drop a class into `energyintelligence/actions/builtin.py`
(or a new module), decorate with `@register`, and implement `validate` (raise
`ActionError` → HTTP 409 when the household lacks the asset) and `execute`
(compute a data-grounded effect, apply via the `adapter`, return an
`ActionEffect`). It appears in `/api/actions` and on the dashboard automatically.

```python
from .base import Action, ActionError, _household, register
from ..models import ActionEffect

@register
class PrecoolHome:
    type = "precool_home"
    label = "Pre-cool before peak"

    def validate(self, conn, household_id, params):
        if not _household(conn, household_id)["heat_pump"]:
            raise ActionError("This household has no heat pump.")

    def execute(self, conn, household_id, params, adapter) -> ActionEffect:
        adapter.apply(household_id, {"action": self.type, ...})
        return ActionEffect(status="executed", message="...", details={...})
```

**Swap in a real backend.** The mock `DeviceAdapter` in `actions/adapters.py`
and the `Phraser` implementations in `ai/` are the seams: replace the adapter to
hit real hardware, or add a `Phraser` for a different LLM provider — both behind
their existing protocol, so nothing downstream changes.
