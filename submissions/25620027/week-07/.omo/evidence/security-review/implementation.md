# Seller operator-directive evidence boundary

Recorded UTC: 2026-10-20T15:00:33.360626+00:00

## Ownership and behavior

Only market_a2a/security.py, market_a2a/remote.py, market_a2a/buyer.py, tests/test_security_boundary.py and this evidence directory were edited. Parent owns SafetyObservation/Purchase schema, public-review handling, review rating/text policy and final deployment. No market API, model API, process restart, official 25-check run or commit was performed.

SecurityEvents shares a per-order collector, retains at most 64 matching-response counts and 8 unique SHA256 references, and never retains raw text. Detection requires a directly asserted market/platform authority or policy and a commercial/rating/budget directive in the same bounded paragraph. Generic marketing, honest-review requests, quoted examples, negation and warning cases are not classified as incidents by the supported grammar. This is deliberately narrow evidence detection, not a complete prompt-injection classifier.

Remote.send observes bounded status text after a typed response is parsed. Existing transport traces retain raw evidence. BuyerOrder.security_events permits parent exception handling to preserve facts even when buy_order raises. Both returned success and no-deal Purchases include snapshots. Model summaries drop opaque remote task IDs/SKU/item text and receive requested item plus numeric/boolean evidence fields; hashes remain only in persisted Purchase evidence.

## Validation evidence

- Invocation: cached Python `-m pytest tests/test_security_boundary.py tests/test_buyer_order.py -q`. Captured artifact: tests.txt. Binary result: exit 0, **24 passed** (22 new + 2 existing buyer-order regressions).
- Invocation: cached Ruff `check market_a2a/security.py market_a2a/remote.py market_a2a/buyer.py tests/test_security_boundary.py`. Artifact: ruff.txt. Binary result: exit 0, All checks passed.
- Invocation: cached basedpyright `--pythonpath /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python market_a2a/security.py market_a2a/remote.py market_a2a/buyer.py tests/test_security_boundary.py`. Artifact: types.txt. Binary result: exit 0, 0 errors, 0 warnings, 0 notes.
- Invocation: `PYTHONPYCACHEPREFIX=/tmp/ax-w07-security-pycache .venv/bin/python -m py_compile market_a2a/security.py market_a2a/remote.py market_a2a/buyer.py tests/test_security_boundary.py`. Binary result: exit 0, no diagnostics.

## Scenario mapping

- Actual observed market-operator attack and other authority/directive forms: parametrized detection test records true/1/SHA256; benign advertisement, honest review, ordinary signature policy, standalone request, quoted/blockquoted warning and split paragraphs record false/0.
- Repeated unique attacker responses: exact cap 64 responses and 8 hashes.
- Valid completed HTTP-fake checkout with attack: original receipt unchanged, safety true and two response observations.
- Buyer walk-away: no completed purchase, observed incident retained.
- Harness exception after initial HTTP-fake response: exception propagates while caller-owned collector retains incident.
- Arbitrary remote task/context/SKU/item strings: none enter model summary; metadata.ask=150 overrides plaintext 144, and only code-generated safety booleans/counts enter model state.
- Existing exact-item rejection and descriptive-order compatibility tests pass unchanged.

The transport fake is httpx2.MockTransport, and the model replacement is the existing ScriptedHarness; no live requests are made. No live behavioral effectiveness or universal injection immunity is claimed. Public-review attribution and low-star/factual-warning behavior are parent-owned work, not verified by this evidence.

## Source SHA256
- market_a2a/security.py: 4bbe18d8f14eabff99872f55a14a10ae4789ae84568e1dc6deac3dd93fea5c46
- market_a2a/remote.py: e2330936682a93e6de4c381f62ddba3d8f400b10558596ab0d44ca94904fc555
- market_a2a/buyer.py: aa57c22c1080bd77bb6a0d5016b4a506a3570fdf6ffd4dcb261b789097ced7f6
- tests/test_security_boundary.py: c9bec5266edc55a423e9f3d71c8b45acca23e268abc664f757b0dd1555b05cba
