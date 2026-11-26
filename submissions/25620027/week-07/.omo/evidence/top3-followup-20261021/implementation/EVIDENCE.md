# Implementation evidence

Scope: catalog.py, seller_turn.py, seller_policy.py, seller.py, assets/seller-ad.html strategy_version, tests/test_search_shortlist.py. No deployment, model/API call, key/environment edit, or Git commit performed.

All final command invocations and exit codes: checks.json. Test transcript: pytest-pass.txt; machine result: pytest-pass.xml. Exact final source diff: implementation.diff (new test file remains untracked and must be included by parent).

| Criterion / exact scenario | Invocation | Binary observable | Artifact |
|---|---|---|---|
| Pre-limit constraint: test_request_filter_before_limit_recovers_seventh_candidate | Cached runtime python -m pytest -q -s | Existing top six all fail dimmable compatibility; original search semantics retained; constrained search returns lamp-z beyond six; limit=0 empty | pytest-pass.xml, pytest-pass.txt |
| Conservative shortlist: test_opening_preserves_model_search_and_decision (8 parameter cases) | Same full pytest invocation | Basic exact requests return 1 candidate; descriptive dimmable returns matching lamp-z; size/color/quantity/extra text preserve 6 variants; exactly search_catalog then decide; model-selected quote107 observed | pytest-pass.txt |
| Quote-range hints | Same parameterized A2A scenario | Every search result carries integer min100/max110; final quote107 emitted without clamping | pytest-pass.txt |
| Card priority: test_card_frontloads_verified_terms signed=true,false | Same full pytest invocation | version1.2.1; first300 contains minima100/180/260,numeric offer,same taskId,dated comparison URL; Ed25519 advertised iff signer present | pytest-pass.txt |
| Existing safety behavior: test_seller_receipt_when_real_a2a_task_accepts_offer and full suite | Same full pytest invocation | 178 passed; signed receipt verifies exact price120; reserve held; replay rejected | pytest-pass.xml, pytest-pass.txt |
| Terms machine version | Python JSON parse of script#shop-terms in assets/seller-ad.html | strategy_version == 1.2.1 | checks.json |
| Static checks scoped to changed Python files | See checks.json for complete arguments | Ruff exit0, basedpyright 0errors/0warnings, git diff --check exit0 | ruff-pass.txt, types-pass.txt, diff-check-pass.txt |

First failures retained: baseline-pytest.txt records .venv missing pytest; ruff-first.txt records style issues; types-first.txt records incorrect interpreter missing pytest; types-final.txt records implicit regex concatenation. Corrected runs above are final. No retrospective attribution of historical 60 failures is claimed. Candidate response character lengths are observed in pytest-pass.txt; no external token/cost/sales improvement is claimed. Colors from the original request are not newly enforced if a model query drops them: preserving variants here protects existing semantics rather than expanding validation scope.
