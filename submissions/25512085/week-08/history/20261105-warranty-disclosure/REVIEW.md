# Warranty disclosure fix and historical review — 2026-11-05

## Scope and fix

The negotiation counteroffer previously used the fixed `CARE_HOURS` value (6), although the deal stored A72/B6/C0 warranty terms. Counteroffers could therefore understate A coverage and promise coverage absent from C receipts.

`seller.py` now derives the effective warranty once for the negotiation branch and uses it for counteroffer text, final rejection text, and the receipt. Stored custom durations and explicit zero take precedence; `no_care` makes effective coverage zero. Only legacy deals lacking the field retain the existing grade-policy fallback. Text uses `care_terms`, also used by receipts. A zero-warranty C unit is not advertised as having an available paid warranty. Food does not acquire goods-warranty text.

Pricing, inventory selection, liquidation rules, and the A72/B6/C0 policy were not changed. No VM deployment or live transaction was performed.

## Offline validation

- Existing grade-warranty baseline: 15 tests passed.
- New regression tests against the old implementation: 12 tests run, 13 failures including subtests. These include additional wording assertions; this count is not a count of distinct production incidents.
- After the fix: new and existing warranty tests, 27 passed.
- Offline discovery: 367 tests passed (`logs/warranty-disclosure-discover-20261105-194350-5610.log`).
- After strengthening the no-alternative assertion: 12 new tests passed.
- Main coordinator independently reran both warranty modules: 27 passed (`logs/warranty-disclosure-main-verification-20261105.log`).
- Regression paths execute the actual seller coroutine, force a low offer to elicit a counteroffer, and then verify the signed receipt. The acceptance price is taken from session state, not parsed from the displayed text.
- Independent read-only review found no blocking issue. The coordinator inspected the actual diff; the reviewer did not independently establish an old/new diff comparison.

Coverage includes A72/B6/C0, stored 24h, stored zero overriding grade policy, no-care, legacy missing-field fallback, food, final rejection, and unavailable warranty alternatives. Fixtures isolate temporary state and forbid network connections.

## Historical evidence and limits

The read-only analysis covered all 14,232 lines in the two VM exports. Test logs were excluded. Original SHA-256 hashes were verified again after the analysis and tests:

| File | SHA-256 |
| --- | --- |
| `logs/market-kit.log` | `8599add99b29939a00c5ba22188df9ac5223e0a4dcc39cc416b30d839ef0957c` |
| `logs/market-kit.log.1` | `f428a8a1e5baf5d23caa8e82ba2260443220a1713fa91664afff6275174e2f62` |

- Neither log records the seller's intermediate `Special concession` response body. Zero occurrences of that text do NOT establish zero real exposures.
- A72 receipts after at least three turns: 6 distinct tasks and units, all with matching unit-and-amount settlement entries. Example: `market-kit.log.1:541` to `:546`, A72, four turns, amount 6240.
- C0 receipts after at least three turns: 9 tasks across 5 units; 4 issuances match 4 settlements. Example: `market-kit.log.1:3038` to `:3040`, C0, three turns, amount 63297.
- These are potential-exposure candidates, NOT confirmed delivery of the wrong text or a complete count of affected transactions.
- Negotiated issuance summaries: 392 rows/tasks, 343 distinct units. Including immediate issuance: 406 rows, 347 units. Settlements: 340 rows and units. Repeated issuance must not be counted as repeated paid sales.
- The 551 buyer receipt entries describe other shops and were not used as evidence of this seller's wording.
- Breakage and buyer-failure entries do not establish a causal link to this disclosure defect. Refund refusal, complaints, or monetary harm caused by the wording could neither be confirmed nor ruled out from these inputs.

No historical log, receipt, or market result was modified. This review does not retroactively change terms or compensate transactions. Live response capture or additional transaction-specific records would be required to establish historical exposure and harm.
