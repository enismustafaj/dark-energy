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
