import { useEffect, useState } from "react";
import { fetchCases, fetchSummary } from "./api";
import type { Case, CaseStatus, Summary } from "./types";
// @ts-ignore - CSS is handled by the bundler; no TypeScript declaration is configured.
import "./index.css";

type FilterValue = "all" | CaseStatus;

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [cases, setCases] = useState<Case[]>([]);
  const [filter, setFilter] = useState<FilterValue>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const [s, c] = await Promise.all([fetchSummary(), fetchCases()]);
      setSummary(s);
      setCases(c);
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Could not reach the API. Is `uvicorn api:app --reload` running?"
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  const visibleCases =
    filter === "all" ? cases : cases.filter((c) => c.status === filter);

  const selectedCase = cases.find((c) => c.transaction_id === selectedId) ?? null;

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>RecoverAI</h1>
          <div className="subtitle">Revenue recovery command center</div>
        </div>
        <button className="refresh-btn" onClick={loadAll} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </header>

      {error && (
        <div className="error-state">
          <div className="title">Couldn't load data</div>
          <div>{error}</div>
        </div>
      )}

      {!error && loading && <div className="loading">Loading batch results…</div>}

      {!error && !loading && summary && (
        <>
          <StatsRow summary={summary} />
          <DistributionBar summary={summary} />
          <GuardrailPanel cases={cases} totalBlocks={summary.guardrail_activations} />
          <ApprovalPanel cases={cases} summary={summary} />
          <LeakagePanel summary={summary} />
          <div className="main">
            <div className="panel">
              <div className="panel-header">
                <h2>Cases ({visibleCases.length})</h2>
                <StatusFilters value={filter} onChange={setFilter} />
              </div>
              {visibleCases.map((c) => (
                <CaseRow
                  key={c.transaction_id}
                  caseData={c}
                  selected={c.transaction_id === selectedId}
                  onClick={() => setSelectedId(c.transaction_id)}
                />
              ))}
              {visibleCases.length === 0 && (
                <div className="detail-empty">No cases with this status.</div>
              )}
            </div>
            <div className="panel">
              <div className="panel-header">
                <h2>Reasoning trail</h2>
              </div>
              {selectedCase ? (
                <CaseDetail caseData={selectedCase} />
              ) : (
                <div className="detail-empty">
                  Select a case on the left to see why the agent did what it did.
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function StatsRow({ summary }: { summary: Summary }) {
  const resolvedPct = summary.total ? summary.resolved / summary.total : 0;
  const handledPct = summary.total
    ? (summary.resolved + summary.escalated) / summary.total
    : 0;
  const baselinePct = summary.total
    ? summary.baseline_avg_resolved / summary.total
    : 0;
  const lift = resolvedPct - baselinePct;

  return (
    <div className="stats">
      <div className="stat">
        <div className="label">Amount at risk</div>
        <div className="value">₹{formatMoney(summary.amount_at_risk)}</div>
      </div>
      <div className="stat">
        <div className="label">Amount recovered</div>
        <div className="value recovered">₹{formatMoney(summary.amount_recovered)}</div>
      </div>
      <div className="stat">
        <div className="label">Resolved rate</div>
        <div className="value">{pct(resolvedPct)}</div>
        <div className="sub">
          {lift >= 0 ? "+" : ""}
          {pct(lift)} vs. naive retry baseline ({pct(baselinePct)})
        </div>
      </div>
      <div className="stat">
        <div className="label">Properly handled</div>
        <div className="value escalated">{pct(handledPct)}</div>
        <div className="sub">resolved + escalated to a human</div>
      </div>
    </div>
  );
}

function DistributionBar({ summary }: { summary: Summary }) {
  const total = summary.total || 1;
  const resolvedW = (summary.resolved / total) * 100;
  const escalatedW = (summary.escalated / total) * 100;
  const unresolvedW = (summary.unresolved / total) * 100;
  const baselineLeft = (summary.baseline_avg_resolved / total) * 100;

  return (
    <div className="distribution">
      <div className="row">
        <div className="title">Outcome breakdown across {summary.total} cases</div>
      </div>
      <div className="bar-track">
        <div className="bar-segment resolved" style={{ width: `${resolvedW}%` }} />
        <div className="bar-segment escalated" style={{ width: `${escalatedW}%` }} />
        <div className="bar-segment unresolved" style={{ width: `${unresolvedW}%` }} />
        <div className="baseline-marker" style={{ left: `${baselineLeft}%` }} />
      </div>
      <div className="legend">
        <span><span className="dot" style={{ background: "var(--accent-recovered)" }} />Resolved ({summary.resolved})</span>
        <span><span className="dot" style={{ background: "var(--accent-escalated)" }} />Escalated ({summary.escalated})</span>
        <span><span className="dot" style={{ background: "var(--accent-unresolved)" }} />Unresolved ({summary.unresolved})</span>
        {summary.errored > 0 && <span>{summary.errored} API errors excluded</span>}
      </div>
    </div>
  );
}

function StatusFilters({
  value,
  onChange,
}: {
  value: FilterValue;
  onChange: (v: FilterValue) => void;
}) {
  const options: FilterValue[] = ["all", "resolved", "escalated", "unresolved", "error"];
  return (
    <div className="filters">
      {options.map((opt) => (
        <button
          key={opt}
          className={`filter-btn ${value === opt ? "active" : ""}`}
          onClick={() => onChange(opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}

function CaseRow({
  caseData,
  selected,
  onClick,
}: {
  caseData: Case;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <div className={`case-row ${selected ? "selected" : ""}`} onClick={onClick}>
      <div className="left">
        <span className={`status-dot ${caseData.status}`} />
        <span className="id">{caseData.transaction_id}</span>
        <span className="cause">{caseData.gateway_response}</span>
      </div>
      <span className="amount">₹{formatMoney(caseData.amount)}</span>
    </div>
  );
}

function GuardrailPanel({
  cases,
  totalBlocks,
}: {
  cases: Case[];
  totalBlocks: number;
}) {
  // Sourced from each case's guardrail_blocks list (the actual data
  // the PolicyEngine records) rather than the history array, since
  // blocked actions are never executed and so never appended to
  // history -- only to guardrail_blocks.
  const blockedEntries = cases.flatMap((c) =>
    (c.guardrail_blocks ?? []).map((block) => ({ caseId: c.transaction_id, block }))
  );

  return (
    <div className="distribution" style={{ marginBottom: 28 }}>
      <div className="row">
        <div className="title">
          Guardrail activity — {totalBlocks} action{totalBlocks === 1 ? "" : "s"} blocked by policy, not the model
        </div>
      </div>
      {blockedEntries.length === 0 ? (
        <div className="detail-empty" style={{ padding: "16px 0" }}>
          No actions were blocked in this batch — every proposed action stayed
          within the policy limits on its own. The retry/message caps are
          proven correct by a passing deterministic test regardless.
        </div>
      ) : (
        blockedEntries.map(({ caseId, block }, i) => (
          <div className="guardrail-row" key={i}>
            <span className="id">{caseId}</span>
            <span className="cause">{block.action}</span>
            <span className="reason">{block.reason}</span>
          </div>
        ))
      )}
    </div>
  );
}

function ApprovalPanel({ cases, summary }: { cases: Case[]; summary: Summary }) {
  // Parallel to GuardrailPanel, but for the AgentVault-style
  // permission tier: money-moving actions above
  // approval_threshold_amount are flagged (never blocked) as needing
  // elevated human sign-off in production.
  //
  // Defensive defaults below: if results.json was generated by an
  // older main.py (before this feature existed), these fields won't
  // be present in the API response yet. Falling back to 0/5000
  // instead of crashing means the dashboard still renders -- the
  // panel just shows "no flags yet" until `python main.py` is re-run.
  const activations = summary.approval_activations ?? 0;
  const threshold = summary.approval_threshold_amount ?? 5000;

  const flaggedEntries = cases.flatMap((c) =>
    (c.approval_flags ?? []).map((flag) => ({ caseId: c.transaction_id, flag }))
  );

  return (
    <div className="distribution" style={{ marginBottom: 28 }}>
      <div className="row">
        <div className="title">
          Approval tiering — {activations} action
          {activations === 1 ? "" : "s"} above ₹
          {formatMoney(threshold)} flagged for elevated approval
        </div>
      </div>
      {flaggedEntries.length === 0 ? (
        <div className="detail-empty" style={{ padding: "16px 0" }}>
          No money-moving action in this batch exceeded the ₹
          {formatMoney(threshold)} approval threshold.
        </div>
      ) : (
        flaggedEntries.map(({ caseId, flag }, i) => (
          <div className="approval-row" key={i}>
            <span className="id">{caseId}</span>
            <span className="cause">{flag.action}</span>
            <span className="reason">
              ₹{formatMoney(flag.amount)} — would require human sign-off in
              production
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function LeakagePanel({ summary }: { summary: Summary }) {
  // Same category-normalization the backend does: gateway_response
  // values map to their known root cause, or "ambiguous_error" for
  // the deliberately messy raw strings. For the checkout trigger,
  // "category" is the drop-off stage instead -- either way, this
  // panel just renders whatever list of categories the batch's
  // results.json shipped.
  const rows = summary.leakage_breakdown ?? [];
  if (rows.length === 0) return null;

  const maxAtRisk = Math.max(...rows.map((r) => r.amount_at_risk), 1);

  return (
    <div className="distribution" style={{ marginBottom: 28 }}>
      <div className="row">
        <div className="title">Revenue leakage by category</div>
      </div>
      {rows.map((row) => (
        <div className="guardrail-row" key={row.category}>
          <span className="id" style={{ minWidth: 150 }}>{row.category}</span>
          <span className="reason" style={{ flex: 1 }}>
            <div style={{ background: "var(--border)", height: 6, borderRadius: 3, marginBottom: 4 }}>
              <div
                style={{
                  width: `${(row.amount_at_risk / maxAtRisk) * 100}%`,
                  background: "var(--accent-unresolved)",
                  height: 6,
                  borderRadius: 3,
                }}
              />
            </div>
            {row.total} cases · {row.resolved} resolved ({pct(row.recovery_rate)}) · ₹
            {formatMoney(row.amount_at_risk)} at risk · ₹{formatMoney(row.amount_recovered)} recovered
          </span>
        </div>
      ))}
    </div>
  );
}

function CaseDetail({ caseData }: { caseData: Case }) {
  return (
    <div>
      <div className="detail-header">
        <div className="id">{caseData.transaction_id}</div>
        <div className="meta">
          {caseData.gateway_response} · ₹{formatMoney(caseData.amount)} · customer{" "}
          {caseData.customer_id}
        </div>
        <span className={`status-badge`}>{caseData.status}</span>
        {(caseData.guardrail_blocks ?? []).length > 0 && (
          <span className="status-badge blocked-badge">
            {caseData.guardrail_blocks.length} action
            {caseData.guardrail_blocks.length === 1 ? "" : "s"} blocked
          </span>
        )}
        {(caseData.approval_flags ?? []).length > 0 && (
          <span className="status-badge approval-badge">
            {caseData.approval_flags.length} action
            {caseData.approval_flags.length === 1 ? "" : "s"} flagged for approval
          </span>
        )}
      </div>
      {caseData.history.length === 0 && (
        <div className="detail-empty">No actions were recorded for this case.</div>
      )}
      {caseData.history.map((step, i) => (
        <div
          className={`history-step ${step.blocked ? "blocked" : ""} ${
            step.requires_approval ? "needs-approval" : ""
          }`}
          key={i}
        >
          <div className="action">
            {step.action}
            {step.blocked && <span className="blocked-tag">blocked</span>}
            {step.requires_approval && (
              <span className="approval-tag">needs approval</span>
            )}
          </div>
          <div className="result">{JSON.stringify(step.result, null, 2)}</div>
        </div>
      ))}
    </div>
  );
}

function formatMoney(n: number): string {
  return n.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}