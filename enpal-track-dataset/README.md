# Enpal Smart Energy Companion — Synthetic Dataset

Fully synthetic, physically plausible data for **4 German households** over the full year **2025** at **15-minute resolution**. Designed to support all four scope areas of the track.

| Scope area | Files |
|---|---|
| Unified energy view | `energy_timeseries_<id>.json` |
| Conversational layer (grounding) | timeseries + `contracts.json` + `monthly_bills.json` |
| Contract & tariff intelligence | `contracts.json`, `tariffs.json`, `dynamic_prices.json` |
| Proactive insights & nudges | `monthly_bills.json`, `insight_events.json` |

## Files

**`households.json`** — metadata + asset config for each home, with a pointer to its timeseries file.

**`energy_timeseries_<id>.json`** — 35,040 records/home. Each record (kW averaged over the 15-min step; multiply by 0.25 for kWh):
`timestamp, outdoor_temp_c, pv_production_kw, house_load_kw, heatpump_kw, ev_charging_kw, total_consumption_kw, battery_charge_kw, battery_discharge_kw, battery_soc_kwh, battery_soc_pct, grid_import_kw, grid_export_kw, price_eur_per_kwh`.
Energy balance holds every step: `pv + grid_import + battery_discharge = total_consumption + grid_export + battery_charge`.

**`tariffs.json`** — two tariffs: `dynamic` (hourly spot + adder) and `fixed` (flat rate). Includes base fees and feed-in rates.

**`dynamic_prices.json`** — 8,760 hourly spot prices (€/kWh), German double-peak shape with midday solar dips and occasional negative summer prices. Retail price = `spot_price + tariff.spot_adder_eur_per_kwh`.

**`contracts.json`** — per-household contract incl. start/end, minimum term, notice period, pricing model, feed-in rate, and a free-text `contract_terms_text` field for parsing/NLP demos.

**`monthly_bills.json`** — 48 records (4 homes × 12 months): consumption, PV, import/export, energy cost, feed-in credit, total bill, self-sufficiency %.

**`insight_events.json`** — pre-detected anomalies & nudges (heat-pump fault week, cheapest-charging-window, high standby/baseload, bill-spike month) to seed or benchmark the proactive layer.

## Households

| ID | Profile | PV | Battery | Heat pump | EV | Tariff |
|---|---|---|---|---|---|---|
| HH-1001 | Munich, 4 ppl | 9.8 kWp | 10 kWh | yes | yes | dynamic |
| HH-1002 | Hamburg, 3 ppl | 6.4 kWp | 7.7 kWh | yes | no | dynamic |
| HH-1003 | Cologne, 5 ppl | 12.2 kWp | 15 kWh | yes | yes | fixed |
| HH-1004 | Berlin, 2 ppl | 4.6 kWp | none | no | no | dynamic |

All values are synthetic and for hackathon use only.
