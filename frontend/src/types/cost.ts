export interface CostSummary {
  total_cost: number;
  budget_limit: number;
  budget_remaining: number;
  percentage_used: number;
  period_start: string;
  period_end: string;
  by_category: CostByCategory[];
  by_model: CostByCategory[];
  daily_costs: DailyCost[];
}

export interface CostByCategory {
  category: string;
  amount: number;
  percentage: number;
  color: string;
}

export interface DailyCost {
  date: string;
  amount: number;
}

export interface CostEntry {
  id: string;
  session_id: string;
  category: string;
  amount: number;
  description: string;
  created_at: string;
}

export interface SessionCostBreakdown {
  session_id: string;
  session_title: string;
  total_cost: number;
  entries: CostEntry[];
}

export interface TurnCost {
  turn_id: string;
  total_cost: number;
  iterations: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  models_used: Record<string, number>;
  tools_used: string[];
}

export interface SessionDetailedCost {
  session_id: string;
  total_cost: number;
  turns: TurnCost[];
  by_model: CostByCategory[];
  cache_efficiency: number;
}
