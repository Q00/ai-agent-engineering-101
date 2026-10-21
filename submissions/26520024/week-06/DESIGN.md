# Week 06 experiment design

Scope: required trade test and challenges 1 and 2 only. No challenge 3, public
deployment, cross-student trading, push or PR. Fictional furniture purchases.
Preserve previous submissions and the staged week-02 ARCHITECTURE.md.

## Shared agents

- Reuse the week-05 tool-free Codex adapter: gpt-6-astra, reasoning low, existing
  login, empty temporary working directory, native tools disabled and audited.
  Role instructions/history are fields in a CLI input JSON wrapper, not native
  OpenAI API system messages. Temperature/seed are not set; internal values unknown.
- Use official a2a-sdk 1.2.0 for A2A v1.0 JSON-RPC server, Agent Cards, task store,
  INPUT_REQUIRED/COMPLETED/REJECTED states, cancellation and receipt artifacts.
  Python buyer sends A2A-Version: 1.0 on GET and POST and preserves taskId.
- Every asking price, counteroffer, acceptance, rejection, buyer offer, walk-away,
  catalog selection and shop/budget plan comes from a real harness response.
  Code validates decisions and serializes the protocol; it never clamps prices,
  silently substitutes a price, auto-accepts or chooses the cheapest shop.
- Seller may ask only at/above the selected product's reserve. Acceptance must
  match the actual incoming offer and respect the reserve. First turn must ask
  or reject, not accept. Product identity becomes immutable after selection.
- Buyer offers cannot exceed its allocation. Receipt price must match the last
  sent offer and stay within budget. Do not retry ambiguous purchase requests.
- Invalid decisions receive explicit validation feedback, at most two corrective
  attempts. Preserve every failed response. Exhaustion is an error, not a fake
  successful negotiation. Bounded model calls and buyer deadline below 300 s.
- Basic messages use a final numeric price; no extra-credit ambiguous-price or
  hostile-message experiment. Finished tasks must return a protocol error.

## Required challenges

1. Commit a deterministic synthetic catalog with at least 3,000 products before
   live calls. Use SQLite FTS5 to retrieve a small candidate set from a natural
   description; the model chooses a candidate, not a query-specific lookup table.
   Freeze six descriptive requests and expected product IDs. A scripted buyer
   accepts the seller's ask; check identity, all asks and receipt against catalog
   reserves. This isolates large-catalog seller behavior, not buyer bargaining.
2. Run 20 local seller instances with distinct advertised skills, inventories
   and prices. Fetch their real cards. A model sees bounded skill summaries and
   chooses one shop and a budget allocation per shopping-list item. Validate
   complete unique coverage and allocations summing to no more than total budget.
   Negotiate different items concurrently. Immutable per-item spending caps and
   one task per item prevent overspending and duplicate purchases. Retain model
   summaries plus typed authoritative state per negotiation, not all conversations
   in one growing prompt. Run three shopping sessions and check actual receipts.

## Evidence and environment

- Current base is Python 3.8.19 with no A2A SDK; SDK 1.2.0 requires Python >=3.10.
  Do not alter any conda environment. User subsequently selected eraseanything
  (Python 3.10.22); its interpreter backs the project-local .venv. Only that
  isolated .venv receives the required packages.
- Source of truth: ../../../weeks/week-06/README.md and unmodified current
  ../../../scripts/trade_test_week06.py. Never replace its SDK or test with a shim.
- Run the official trade test, preserve a complete archive before any rerun, and
  keep the final genuine test-report.json and logs at the required paths.
- Real logs contain model input, raw output/usage, selected actions, guard errors,
  protocol messages, task IDs, retrieval candidates, plans and receipts. No keys,
  tokens, authentication files, or real purchases. Offline scripted tests are
  labeled synthetic and are not reported as model experiments.
- Record failed attempts and fixes, commit progressively, and write a Korean
  report grounded in actual log lines. Passing the structural checker alone does
  not demonstrate the challenges. Independently verify completed outcomes.

References: https://a2a-protocol.org/v1.0.0/specification/ and
https://a2aproject.github.io/a2a-python/docs/migrations/v1_0/.
