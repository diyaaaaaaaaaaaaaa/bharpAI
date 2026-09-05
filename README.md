# RecoverAI

**Razorpay Buildathon — Track 03: AI Revenue Recovery**

An agent that detects revenue at risk, diagnoses the cause, and executes a
bounded, gated recovery action — with a measured comparison against a naive
baseline, deterministic guardrails proven by unit tests, and an honest audit
trail of everything it did and didn't do.

Built solo by Prachi, 3rd-year B.Tech ECE, IIIT Naya Raipur.

---

## What it does

Two working trigger sources run on the same underlying engine:

| Trigger | What it watches | Status |
|---|---|---|
| **Payment failure recovery** | Failed transactions (card declines, insufficient funds, network timeouts, bank server errors, ambiguous gateway errors) | Fully built, tested, and benchmarked |
| **Checkout drop-off recovery** | Abandoned checkout sessions (cart / shipping / payment-details / OTP-verification stages) | Fully built, tested, and benchmarked — added second to prove the architecture actually generalizes, not just to pad the feature list |

Both run: **diagnose (LLM) → propose one bounded action (LLM) → gate the
action (deterministic policy) → execute (deterministic, simulated outcome)
→ log (audit trail) → repeat until resolved, escalated, or explicitly
marked unresolved.**

The LLM only ever does two things: interpret messy signals into a cause, and
pick one action from a small, fixed list. Every rule that decides whether an
action is *allowed to run* — retry caps, no-repeat-contact, discount stacking,
amount-based approval tiers — is plain deterministic Python. The LLM proposes;
policy decides. This split is proven, not just claimed: see [Guardrails are
tested, not assumed](#guardrails-are-tested-not-assumed) below.

---

## Architecture

```
                 ┌─────────────────┐
  batch input →  │   TriggerSource  │   (payment txns / checkout sessions)
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │   Diagnoser (LLM) │   interprets cause, proposes ONE action
                 │  Gemini, via a    │   from a small fixed tool list
                 │  model-fallback + │
                 │  timeout-hardened │
                 │  call layer       │
                 └────────┬─────────┘
                          │ proposed action
                 ┌────────▼─────────┐
                 │   PolicyEngine    │   deterministic, no LLM involved:
                 │  (allowed/blocked │   retry caps, contact caps, discount
                 │  + requires_      │   stacking, amount-based approval tier
                 │  approval)        │   → PROVEN by unit tests
                 └────────┬─────────┘
                          │ if allowed
                 ┌────────▼─────────┐
                 │  ActionRegistry / │   executes the action, returns a
                 │  Executor         │   simulated (but honestly-modeled)
                 │                   │   outcome — every action type has its
                 │                   │   own real chance of resolving, not
                 │                   │   just one "primary" action
                 └────────┬─────────┘
                          │
                 ┌────────▼─────────┐
                 │    AuditLog       │   every decision, blocked or not,
                 │ (JSON-lines,      │   written to one shared trail —
                 │  shared across    │   tagged by trigger so payment and
                 │  both triggers)   │   checkout decisions can be read
                 └───────────────────┘   interleaved
```

Each trigger source has its own case model, its own action registry, its own
prompt, and — deliberately — its own self-contained policy rules (checkout's
guardrails don't live in the payment engine's `policy_engine.py`, and vice
versa, so neither can accidentally break the other). What *is* shared: the
hardened LLM-calling machinery (model fallback chain, per-call timeout,
retry-with-backoff) and the audit trail. That's the concrete evidence that
this architecture is pluggable, not hardcoded to one problem — the second
trigger source is proof, not a slide.

### Files

```
backend/
├── models.py              # Transaction, RecoveryCase (payment engine)
├── tools.py                # Payment action registry + simulated executor
├── policy_engine.py        # Payment guardrails: retry/message caps,
│                            #   AgentVault-style amount-based approval tier
├── prompts.py               # Payment diagnosis system prompt
├── agent.py                 # Payment engine's core loop + shared LLM
│                            #   plumbing (model fallback / timeout / retry,
│                            #   reused directly by checkout_trigger.py)
├── audit_log.py             # Shared JSON-lines audit logger
├── generate_dataset.py      # Payment batch generator (weighted causes +
│                            #   deliberately ambiguous gateway strings)
├── run_baseline.py          # Naive "always retry once" baseline
├── test_policy_engine.py    # 9 deterministic guardrail tests, no LLM
├── main.py                  # Payment batch entry point → results.json
├── api.py                   # FastAPI serving results.json to the dashboard
├── checkout_trigger.py      # Second trigger, fully self-contained: own
│                            #   case model, action registry, policy rules,
│                            #   prompt, dataset generator, run loop, and
│                            #   entry point → checkout_results.json
└── test_checkout_policy.py  # 10 deterministic guardrail tests, no LLM

frontend/
└── src/
    ├── App.tsx    # Dashboard: stats, distribution, guardrail panel,
    │               #   approval-tiering panel, leakage-by-category panel,
    │               #   filterable case list, per-case reasoning trail
    ├── types.ts
    ├── api.ts
    └── index.css   # Dark ledger/control-room design system
```

Run: `uvicorn api:app --reload` (backend) + `npm run dev` (frontend), two
terminals. Payment batch: `python main.py`. Checkout batch:
`python checkout_trigger.py`.

---

## Key design decisions

**LLM vs. deterministic split.** The LLM only diagnoses and picks from a
bounded action list. Every hard rule that gates a money-adjacent action is
plain Python — this is the "AI judgment: the right tool in the right place,
and where you chose not to use one" rubric line, made literal.

**Every action type has its own real chance of succeeding**, not just one
"primary" action. Early on, only `retry_payment` could resolve a payment
case — `offer_alt_method` and `send_recovery_message` had no real success
path, which made the agent structurally *worse* than a naive retry-only
baseline even when it was making smarter choices. Fixed by giving every
action its own simulated success probability. The same principle was applied
from the start in the checkout trigger.

**AgentVault-style permission tiering, applied honestly to two different
kinds of risk.** An AI agent moving or giving away real money shouldn't have
blanket authority. In the payment engine, any money-moving action on a
transaction above ₹5,000 gets flagged `requires_approval` (allowed to run in
this batch simulation, but logged and surfaced as something that would need
human sign-off in production). In the checkout trigger, there's no
transaction amount to gate on — so the tier is keyed on the actual value at
risk in that domain: discount value given away (`cart_value × percent_off`),
flagged above ₹300. Same underlying idea, two honest, non-copy-pasted
applications of it.

**Model fallback chain, ordered by quota, and updated live.** Google's
`-latest` alias models get a very small free-tier daily quota (confirmed:
20 requests/day/model), and — discovered mid-build — both of this project's
`-latest` aliases currently resolve to the *same* underlying model for quota
purposes, meaning a "3-model" fallback chain was really only 2 independent
quota pools. Fixed by pinning a genuinely distinct model
(`gemini-3.5-flash-lite`) as the middle entry. See "What broke" below.

**Hard call timeout via `ThreadPoolExecutor`.** The SDK's own internal retry
logic once silently stacked with this project's retry logic and caused a
multi-hour hang. Fixed with a hard 45s ceiling per call, independent of what
the SDK is doing underneath.

**Guardrail proof is a unit test, not a hoped-for live example.**
`test_policy_engine.py` and `test_checkout_policy.py` prove the policy
engines block/flag exactly at their limits, independent of whether any given
live batch happens to trigger them. **19/19 tests passing, no LLM required to
run them.**

---

## Guardrails are tested, not assumed

```
$ python test_policy_engine.py
9/9 guardrail tests passed

$ python test_checkout_policy.py
10/10 checkout guardrail tests passed
```

Covers: retry/reminder caps at and below the limit, message/discount
no-repeat-contact rules, escalation never being blockable (it's the safety
valve, not a limited resource), and the approval-tiering boundary in both
engines — including the exact-threshold edge case (strict `>`, not `>=`),
and confirming non-money-moving actions are never flagged regardless of
transaction size.

---

## Honest metrics

### Payment failure recovery — 70 cases

| Metric | Value |
|---|---|
| Resolved | 42 (60%) |
| Escalated | 9 (13%) |
| Unresolved | 19 (27%) |
| Guardrail blocks | 0 |
| Approval flags | 20, across 19 cases (amount > ₹5,000) |
| ₹ at risk | ₹2,66,150.86 |
| ₹ recovered | ₹1,61,185.17 (61%) |
| **Naive retry-only baseline** | **31.9/70 (46%)** |
| **RecoverAI vs. baseline** | **+14 points** |

**Leakage by root cause:**

| Category | Cases | Resolved | ₹ at risk |
|---|---|---|---|
| insufficient_funds | 18 | 5 (28%) | ₹75,517 |
| network_timeout | 20 | 15 (75%) | ₹56,202 |
| bank_server_error | 13 | 13 (100%) | ₹51,135 |
| card_declined | 11 | 6 (55%) | ₹46,950 |
| ambiguous_error | 8 | 3 (38%) | ₹36,347 |

Bank server errors and network timeouts — the causes worth persistent
retrying — resolve at 75-100%. Insufficient funds and card declines, where
retrying rarely helps, resolve lower, which is the expected and correct
shape for this policy, not a weakness.

### Checkout drop-off recovery — 40 cases (pre-fix run; see below)

| Metric | Value |
|---|---|
| Resolved | 10 (25%) |
| Escalated | 6 (15%) |
| Unresolved | 24 (60%) |
| Errored | 0 |
| ₹ cart value at risk | ₹1,25,288.73 |
| ₹ cart value recovered | ₹28,444.29 (23%) |
| Approval flags | 9 (discount value > ₹300) |
| **Naive "always send one reminder" baseline** | **8.8/40 (22%)** |

**These numbers are from the run *before* the closure-logic fix described in
"What broke" below** — most of that 60% unresolved figure was the bug, not a
real ceiling on the agent's ability. A corrected re-run (small batch first,
to respect the free-tier quota) is the next step; update this table with the
fresh numbers once confirmed. Leaving the pre-fix numbers here rather than
deleting them on purpose — the before/after is a stronger, more honest story
than a single clean number would be.

---

## What broke (and what we did about it)

Three real incidents, all from the same night, in the order they surfaced:

**1. The daily quota is much smaller than it looks.** The free tier isn't
20 requests total — it's **20 requests per day, per model.** Running the
70-case payment batch and then, same day, a 40-case checkout batch burns
through that fast: each case can take several LLM calls across the
diagnose→act loop, so two batches easily need 150-300+ calls against a
20/day/model ceiling. The result was a wall of `429 RESOURCE_EXHAUSTED`
errors and a batch that was 80% errored cases. Fix: for iteration, run small
batches (10-15 cases) that stay inside quota, and only run the full-size
batch once, right after a quota reset, to pull final numbers.

**2. A model in the fallback chain had been silently deprecated.** Mid-batch,
`gemini-2.5-flash-lite` started returning `404 NOT_FOUND` — "this model is no
longer available to new users." The API's own error told us the exact
replacement (`gemini-3.5-flash-lite`), which is now pinned in the chain.
Bonus discovery while investigating: **both `-latest` aliases in this
project's fallback chain currently resolve to the same underlying model**
(confirmed via the quota error naming the same resolved model for both), so
a "3-model" fallback chain was actually only 2 independent quota pools. The
pinned model fixes that too.

**3. Most "unresolved" checkout cases weren't actually failing — they were
mislabeled.** After fixing #1 and #2, the checkout trigger still resolved
only 25% of cases, with 60% marked unresolved and the audit log claiming
`"reason": "max_iterations_reached"` on almost all of them. Tracing one case
(`CART0040`) end to end: it sent exactly **one** reminder, got a
`sent_no_response` result, and closed — on turn 1 of a 5-turn budget, not
turn 5. The reason string was wrong: the loop actually exits the instant the
model stops proposing a tool call, and the closing code labeled *every* such
exit "max_iterations_reached" regardless of which turn it happened on. Once
logging was fixed to tell the two cases apart, the real behavior was clear:
the model was treating a failed reminder as "sent, wait and see" — reasonable
real-world intuition (a customer might reply to an SMS hours later), but
wrong for this synchronous batch simulation, where every tool result is
already final. Fix: the checkout prompt now explicitly states that every
action's outcome is immediate and final within the simulation, and that the
model must not end its turn on plain text unless it has already called
`escalate_to_human` or `mark_unresolved`. Re-verification is the next step
before the numbers above are final.

The throughline across all three: **the failure was never "the AI made a bad
call" — it was infrastructure and labeling problems that made it hard to see
what the AI was actually doing.** Fixing the visibility (an honest reason
string) is what surfaced the real, fixable cause.

---

## Why the crowded field isn't a problem

Most other Track 03 submissions converge on the same shape — diagnose →
score/prioritize → recommend or execute → track outcome — because that's
what the brief asks for, not because everyone copied each other. What
differs is whether it's proven or just described:

- **A real, controlled baseline comparison.** Most submissions show only
  their own numbers. This one runs a naive baseline on the *same* batch and
  reports the delta honestly (+14 points on payment recovery).
- **A passing unit test for the guardrail mechanism**, on both trigger
  sources, independent of the LLM. Essentially nobody else mentions testing
  this.
- **An honestly-reported error/exception rate**, including a live batch that
  was 80% errors for a real, diagnosable reason — not a suspiciously clean
  100%.
- **A second, real trigger source**, not a stub, proving the "pluggable
  architecture" claim with working code instead of a paragraph.

---

## Known limitations

- Free-tier LLM quota (20 req/day/model) constrains how often full batches
  can be re-run; see "What broke" above.
- Guardrail *blocks* (as opposed to approval *flags*) haven't fired in a live
  batch yet on either engine — correctness is proven by unit test instead,
  which is arguably stronger evidence since it's independent of whether any
  particular random batch happens to trigger the limit.
- Checkout trigger's corrected numbers (post closure-logic fix) are pending
  re-verification as of this writing.
- No live human-in-the-loop UI for the approval-tiering flags — in this
  batch simulation, flagged actions still execute; the flag documents what
  would require sign-off in production, it doesn't gate it yet.