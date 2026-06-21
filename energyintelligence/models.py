"""Pydantic models — the data contracts shared across layers.

``TelemetryRecord`` is the *unified* per-step record. The seed loader, the live
ingest endpoint, and the analytics layer all speak this one shape, which is what
lets analytics be completely source-agnostic.

``DeviceReading`` is the per-device slice a simulator/device actually knows about;
``ingest.mapping`` merges readings into a ``TelemetryRecord``.

``Fact`` / ``FactBundle`` are the contract handed to the AI phrasing layer: the
backend computes every number, the phraser may only rephrase them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DeviceType = Literal["pv", "battery", "heatpump", "ev", "household"]
RESOLUTION_MINUTES = 15


def _snap_to_grid(ts: datetime, minutes: int = RESOLUTION_MINUTES) -> datetime:
    """Strip tzinfo and snap to the resolution grid (floor). Timestamps in this
    system are naive local throughout."""
    ts = ts.replace(tzinfo=None)
    discard = (ts.minute % minutes)
    return ts.replace(minute=ts.minute - discard, second=0, microsecond=0)


class TelemetryRecord(BaseModel):
    """One unified energy step. kW values are averaged over the step; multiply
    by 0.25 for kWh at 15-minute resolution."""

    household_id: str
    ts: datetime
    outdoor_temp_c: float | None = None
    pv_production_kw: float = 0.0
    house_load_kw: float = 0.0
    heatpump_kw: float = 0.0
    ev_charging_kw: float = 0.0
    total_consumption_kw: float = 0.0
    battery_charge_kw: float = 0.0
    battery_discharge_kw: float = 0.0
    battery_soc_kwh: float = 0.0
    battery_soc_pct: float = 0.0
    grid_import_kw: float = 0.0
    grid_export_kw: float = 0.0
    price_eur_per_kwh: float | None = None
    source: Literal["seed", "live"] = "live"

    @field_validator("ts")
    @classmethod
    def _normalize_ts(cls, v: datetime) -> datetime:
        return _snap_to_grid(v)

    def balance_residual(self) -> float:
        """|pv + import + discharge − (consumption + export + charge)| in kW."""
        lhs = self.pv_production_kw + self.grid_import_kw + self.battery_discharge_kw
        rhs = self.total_consumption_kw + self.grid_export_kw + self.battery_charge_kw
        return abs(lhs - rhs)

    @classmethod
    def from_dataset_record(cls, household_id: str, rec: dict) -> "TelemetryRecord":
        """Build from a raw dataset record (keyed by ``timestamp``)."""
        return cls(
            household_id=household_id,
            ts=datetime.fromisoformat(rec["timestamp"]),
            outdoor_temp_c=rec.get("outdoor_temp_c"),
            pv_production_kw=rec.get("pv_production_kw", 0.0),
            house_load_kw=rec.get("house_load_kw", 0.0),
            heatpump_kw=rec.get("heatpump_kw", 0.0),
            ev_charging_kw=rec.get("ev_charging_kw", 0.0),
            total_consumption_kw=rec.get("total_consumption_kw", 0.0),
            battery_charge_kw=rec.get("battery_charge_kw", 0.0),
            battery_discharge_kw=rec.get("battery_discharge_kw", 0.0),
            battery_soc_kwh=rec.get("battery_soc_kwh", 0.0),
            battery_soc_pct=rec.get("battery_soc_pct", 0.0),
            grid_import_kw=rec.get("grid_import_kw", 0.0),
            grid_export_kw=rec.get("grid_export_kw", 0.0),
            price_eur_per_kwh=rec.get("price_eur_per_kwh"),
            source="seed",
        )


# Maps each device type to the telemetry columns it owns.
DEVICE_COLUMNS: dict[DeviceType, tuple[str, ...]] = {
    "pv": ("pv_production_kw",),
    "battery": (
        "battery_charge_kw",
        "battery_discharge_kw",
        "battery_soc_kwh",
        "battery_soc_pct",
    ),
    "heatpump": ("heatpump_kw",),
    "ev": ("ev_charging_kw",),
    "household": ("house_load_kw",),
}


class DeviceReading(BaseModel):
    """A single device reporting its own slice of a step."""

    household_id: str
    device_type: DeviceType
    ts: datetime
    metrics: dict[str, float] = Field(default_factory=dict)
    outdoor_temp_c: float | None = None
    price_eur_per_kwh: float | None = None

    @field_validator("ts")
    @classmethod
    def _normalize_ts(cls, v: datetime) -> datetime:
        return _snap_to_grid(v)


# --- AI phrasing contract --------------------------------------------------

class Fact(BaseModel):
    """A computed, grounded fact. The phraser may rephrase ``numbers`` but must
    never introduce a number not present here."""

    key: str
    household_id: str
    type: Literal["anomaly", "nudge", "insight"] = "insight"
    category: Literal["fault", "contract", "device_choice", "utilization"] = "fault"
    device_id: int | None = None
    severity: Literal["info", "warning", "high"] = "info"
    period: str = ""
    title: str = ""
    detail: str = ""
    numbers: dict[str, float | int | str] = Field(default_factory=dict)
    template_id: str = ""
    suggested_action_key: str | None = None


class Advice(BaseModel):
    """What a rule recommends, with the counterfactually re-evaluated cost.

    ``benefit_eur`` is the annualized customer cost benefit used for ranking."""

    description: str = ""
    baseline_cost_eur: float | None = None
    counterfactual_cost_eur: float | None = None
    benefit_eur: float = 0.0
    capex_eur: float | None = None
    payback_years: float | None = None
    catalog_ref: str | None = None
    action_key: str | None = None


class RuleResult(BaseModel):
    """A fired rule: a grounded Fact plus its optional Advice."""

    fact: Fact
    advice: Advice | None = None

    @property
    def benefit_eur(self) -> float:
        return self.advice.benefit_eur if self.advice else 0.0


class FactBundle(BaseModel):
    household_id: str
    facts: list[Fact] = Field(default_factory=list)
    context: dict[str, str] = Field(default_factory=dict)  # strings only


class PhrasedInsight(BaseModel):
    fact_key: str
    title: str
    body: str
    action_label: str | None = None


# --- Action contract -------------------------------------------------------

class ActionEffect(BaseModel):
    status: Literal["executed", "failed"] = "executed"
    message: str = ""
    expected_savings_eur: float | None = None
    schedule: dict | None = None
    details: dict = Field(default_factory=dict)
