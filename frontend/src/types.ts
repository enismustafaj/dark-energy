export type Household = {
  household_id: string;
  name: string;
  city: string;
  tariff_id: string;
};

export type Hub = {
  annual_cost_eur: number;
  consumption_kwh: number;
  pv_production_kwh: number;
  pv_self_consumption_pct?: number | null;
  month_to_date_cost_eur: number;
  month_estimated_cost_eur: number;
  prev_month_cost_eur?: number | null;
};

export type NodeKind = "device" | "contract";

export type EnergyNode = {
  kind: NodeKind;
  device_id: number | null;
  category: string;
  icon: string;
  label: string;
  metric: string;
};

export type AdviceProjection = {
  baseline_cost_eur?: number | null;
  counterfactual_cost_eur?: number | null;
  payback_years?: number | null;
};

// Compact, grounded data the card renders as a chart in its "How this works" panel.
export type AdviceViz =
  | { kind: "monthly_bills"; series: { month: string; total_eur: number }[]; high_month?: string; low_month?: string }
  | { kind: "hourly_price"; by_hour: { hour: number; price: number }[]; cheap_hour?: number }
  | { kind: "baseload"; baseload_kw: number; avg_load_kw: number }
  | { kind: "grid_independence"; now_eur: number; after_eur: number; capacity_kwh?: number | null }
  | { kind: "tariff_compare"; current_eur: number; alternative_eur: number };

export type Advice = {
  fact_key: string;
  category: string;
  device_id: number | null;
  severity: "info" | "warning" | "high" | string;
  status: "open" | "resolved" | string;
  title: string;
  body: string;
  benefit_eur: number | null;
  advice: AdviceProjection | null;
  action_type: string | null;
  action_label: string | null;
  agent_actionable: boolean;
  numbers?: Record<string, number | string>;
  viz?: AdviceViz | null;
};

export type AppliedAdvice = {
  fact_key: string;
  title: string | null;
  benefit_eur: number | null;
  applied_at: string | null;
};

export type HouseholdView = {
  household: Household;
  hub: Hub | null;
  nodes: EnergyNode[];
  advice: Advice[];
  applied_advice: AppliedAdvice[];
  realized_savings_eur: number;
};

export type ActionEvent = {
  action_type: string;
  label: string;
  message: string;
  status: string;
  expected_savings_eur?: number | null;
  resolved_fact_key?: string | null;
};

export type ChatTurn = {
  role: "user" | "agent";
  text: string;
};

export type ChatReply = {
  message: string;
  source: "openai" | "fallback";
};
