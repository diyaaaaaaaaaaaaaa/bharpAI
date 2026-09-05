export type CaseStatus = "resolved" | "escalated" | "unresolved" | "error";

export interface HistoryEntry {
  action: string;
  result: Record<string, unknown>;
  blocked?: boolean;
  requires_approval?: boolean;
}

export interface GuardrailBlock {
  action: string;
  input: Record<string, unknown>;
  reason: string;
}

export interface ApprovalFlag {
  action: string;
  input: Record<string, unknown>;
  amount: number;
}

export interface Case {
  transaction_id: string;
  customer_id: string;
  customer_name: string;
  payment_method: string;
  created_at: string;
  amount: number;
  gateway_response: string;
  status: CaseStatus;
  attempts_made: number;
  messages_sent: number;
  // NOTE: these are lists (one entry per blocked/flagged action), not
  // counts -- matches what main.py actually writes to results.json.
  guardrail_blocks: GuardrailBlock[];
  approval_flags: ApprovalFlag[];
  history: HistoryEntry[];
}

export interface LeakageCategory {
  category: string;
  total: number;
  resolved: number;
  recovery_rate: number;
  amount_at_risk: number;
  amount_recovered: number;
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
  guardrail_activations: number;
  cases_with_a_guardrail_block: number;
  approval_activations: number;
  cases_requiring_approval: number;
  approval_threshold_amount: number;
  leakage_breakdown: LeakageCategory[];
}