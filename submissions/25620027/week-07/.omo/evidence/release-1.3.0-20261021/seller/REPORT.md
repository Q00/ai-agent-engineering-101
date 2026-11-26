# Seller release evidence

Owned source: seller_turn.py, seller_policy.py, seller_messages.py, new seller_offer.py; new tests/test_seller_offer_release.py. Shared edits preserved. No deployment, API calls, commits, or unrelated file changes.

Runtime prefix: `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/`.
Working directory: submission `submissions/25620027/week-07`.
Artifacts below are relative to this report directory.

| Criterion/scenario | Exact invocation after runtime prefix | Binary observable | Captured artifact |
| --- | --- | --- | --- |
| Final ask 100, ordinary lamp or explicit low offer 99; exact-price reply completes; ask 110 retains coupon possibility | `python -m pytest tests/test_seller_offer_release.py -q -s` | 13 passed; real A2A quote metadata boolean price_final, last text integer equals ask, input_required then completed | `scenarios-pass.log` |
| Selected SKU advertises decide only; rogue search rejected without catalog call | same invocation | tools final two calls both decide-only; exactly one seller.search; rejected-search feedback; completed receipt retains lamp | `scenarios-pass.log` |
| First explicit offer accepts only after model retrieval and corrected SKU/price; both grammar alternatives | same invocation | real A2A completed; exact 100 signed receipt cryptographically verified; terminal replay error; invalid model calls corrected | `scenarios-pass.log` |
| Budget, height, quantity, loose item+number and malformed messages do not parse as offer | same invocation | eight negative parser cases pass | `scenarios-pass.log` |
| Existing price guards, coupon accounting, settlement and signed wire routes | `python -m pytest -q` | 216 passed, zero failures | `full-offline.log` |
| Strict types | `basedpyright --pythonpath /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python market_a2a/seller_turn.py market_a2a/seller_policy.py market_a2a/seller_messages.py market_a2a/seller_offer.py tests/test_seller_offer_release.py` | 0 errors, 0 warnings | `types-final.log` |
| Lint | `ruff check market_a2a/seller_turn.py market_a2a/seller_policy.py market_a2a/seller_messages.py market_a2a/seller_offer.py tests/test_seller_offer_release.py` | All checks passed; custom SIZE_OK directive emits parser warning | `lint.log` |
| Programming skill audit | `python /Users/sungjinho/.codex/plugins/cache/sisyphuslabs/omo/5.1.40/skills/programming/scripts/python/check-no-excuse-rules.py market_a2a/seller_turn.py market_a2a/seller_policy.py market_a2a/seller_messages.py market_a2a/seller_offer.py tests/test_seller_offer_release.py` | no violations in 5 files | `no-excuse.log` |

Grammar: case-insensitive, surrounding whitespace permitted; exactly `offer ITEM N` or `buy ITEM for N`, ITEM one of lamp/chair/bookshelf; N 1–9 ASCII digits. No generic numeric extraction. Bare numeric initial message remains no offer. Followup parsing unchanged. Price decisions remain model-owned with rejected invalid actions, no clamps or synthetic decisions.

Journal seam implemented: optional PersistentSellerStore; await save_negotiation before quote publication; await close before failure/rejection/receipt publication, then mark local state closed. Persistence restart/IO error scenarios are owned and verified by parent, not claimed here.

Retained failed attempt `scenarios.log`: test receipt validator initially rejected protobuf float and extra SKU, corrected test normalization only. `types.log` was generated before parent store module existed. Latest applicable artifacts above supersede those failures.

Skill retrospective: no reusable skill change justified; plugin-managed programming skill was not edited.
