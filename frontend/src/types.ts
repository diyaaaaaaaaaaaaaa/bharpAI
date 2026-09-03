export type CaseStatus = "resolved" | "escalated" | "unresolved" | "error";

export interface HistoryEntry {
  action: string;
  result: Record<string, unknown>;
  blocked?: boolean;
}

export interface Case {
  transaction_id: string;
  customer_id: string;
  amount: number;
  gateway_response: string;
  status: CaseStatus;
  attempts_made: number;
  messages_sent: number;
  guardrail_blocks: number;
  history: HistoryEntry[];
}

export interface Summary {
  total: number;
  resolved: number;
  escalated: number;
  unresolved: number;
  errored: number;
  amount_at_risk: number;
  amount_recovered: number;
  baseline_avg_resolved: number;
  guardrail_blocks: number;
}