# Seller prose isolation in buyer negotiation
Recorded UTC: 2026-10-20T12:43:35.544614+00:00

Changed source ownership: market_a2a/remote.py and market_a2a/buyer.py only. Parent owns deployment and market selection changes.

## Behavior
- Metadata numeric ask is preferred. Otherwise only the first line of three explicit quote formats is fullmatched; appended paragraphs do not enter the model. Unsupported text yields null.
- Protocol state is allowlisted; arbitrary state strings become UNKNOWN.
- Initial and later recent observations and seller-response tool feedback contain only state and numeric ask. Raw observation remains available for logs and failure evidence.
- Existing SendMessage identity, price choice, budget and checkout guards preserved. No live model or marketplace calls performed.

## Verification
Syntax: PYTHONPYCACHEPREFIX=/tmp/ax-w07-boundary-pycache .venv/bin/python -m py_compile market_a2a/remote.py market_a2a/buyer.py (exit 0).
Offline invocation: PYTHONPATH=. PYTHONPYCACHEPREFIX=/tmp/ax-w07-boundary-pycache .venv/bin/python .omo/evidence/injection-boundary/check_boundary.py
Observed binary result: exit 0; 13/13 named checks PASS. Full captured output: checks.txt. Executable scenarios: check_boundary.py. Uses httpx2.MockTransport with no network.

Covered: appended attack extracts 150; raw evidence preserved; metadata priority; unsupported text and inline suffix null; unknown status normalized; metadata strings rejected; initial and follow-up summary exclude attack; tool feedback structured; task ID and numeric proposal retained; overbudget causes no HTTP call.

Not claimed: complete injection immunity, model behavioral equivalence, live deployment or 25-check suite. Structured item/SKU identity and separate market selection/review paths retain their existing contracts; this patch targets seller response prose in negotiation.

## Source hashes
- remote.py: 797236630c695cf2cbc3d8bc187d09af5bc94e5cf1450dace08a5b3ec07cfce0
- buyer.py: 764614668dd8cce88c6021a0e4dd79572aecea0b6bfe22ada96d1249e97ed876

Reviewer compatibility correction: added anchored official `{offer} is too low. Counteroffer: {ask}.` first-line grammar. Its deterministic appended-attack scenario returns only state and ask. Re-ran syntax and all 13 offline scenarios successfully. Ruff check of remote.py and buyer.py also passed.
