# Week 06 independent code review

Reviewed baseline: upstream/main 98968bd0ab5d00fc27f93bdff634f5f60184f596 through fc4fc08 and the completed, uncommitted 24-episode evidence. Scope: submissions/25620027/week-06 only. Read-only source review; this report is the sole reviewer-created artifact. No paid calls or server launches.

codeQualityStatus: WATCH
recommendation: APPROVE
blockers: []

This approval covers code, completed experiment evidence, and the final REPORT.md reviewed in the follow-up below. No ulw-loop plan exists (ULW_LOOP_PLAN_MISSING), so the fallback evidence path is used.

## Findings

### CRITICAL
None.

### HIGH
None.

### MEDIUM
1. Configuration exposes controls that do not fully control runtime behavior. models.py:52 accepts arbitrary positive max_turns, but buyer.py:96 always tells the model six offers. models.py:56 accepts arbitrary opening_multiplier above one, but seller_state.py:59 always computes ceil(reserve * 1.3). Current committed settings are 6 and 1.3, so the observed experiment is consistent. A later settings-only reproduction can silently misstate its conditions. Either constrain these to the fixed supported values or connect them to their actual consumers; avoid changing experimental behavior between conditions.

### LOW
1. run.py:115 checks endpoint readiness and then process liveness once. With another process already holding the configured port, the old endpoint can respond before the newly spawned server reports its bind failure. No collision was observed in the actual logs. Serial isolated execution is the verified operating condition; concurrent execution is not verified.

## Skill-perspective check

Loaded and consulted omo:programming, its Python README and mandatory httpx2 reference, and omo:remove-ai-slops. Applied their read-only review criteria to production and tests. No deletion-only tests, requested-removal tests, tautologies, implementation-constant mirrors, untyped escape hatches, or unnecessary production data extraction were found. Pydantic parsing is at configuration/model/protocol boundaries and is justified. The MEDIUM finding is a programming/maintenance issue: configuration implies unsupported variability.

Buyer tests compare input-card preservation, history transmission, budget role placement, and condition control equality; they do not pin natural-language instruction prose. Seller and runner tests exercise real SDK ASGI routes, actual receipts, rejection boundaries, cancellation, budget excess, final-turn completion, no-choice preservation, and resume. Model HTTP fakes stand in for paid SaaS at the wire boundary.

The auxiliary AST audit objects to assert_never after two exhaustive matches in episode.py. Inspection confirms every case returns, raises, or continues and the fallthrough is assert_never. The real strict type checker passes. This is an auxiliary checker shape mismatch, not missing exhaustiveness; do not claim the AST audit itself passed.

## Independently observed verification

- pytest: 28 passed in 0.99s using /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python3.
- Ruff ALL: All checks passed.
- basedpyright with --pythonpath pointing to that interpreter: 0 errors, 0 warnings, 0 notes. An initial invocation without --pythonpath selected the wrong import environment and reported missing dependencies; corrected invocation passed.
- Read official weeks/week-06/README.md, repo AGENTS.md, PLAN.md, source, tests, configuration, cards, CSV, and raw per-run logs. Scenario preregistration commit 292f447 precedes implementation/run evidence.
- Independently compared all 24 CSV records to parsed [RESULT] records in logs/plain-01.txt through plain-03.txt and inflated-01.txt through inflated-03.txt by condition/run/scenario: exact match, unique 24 keys.
- CSV llm_calls total 49 equals raw BUYER_RESPONSE count; turns total 11 equals actual offer proposals. Raw [TASK] observations include 25 INPUT_REQUIRED and 10 COMPLETED transitions.
- Parsed pricey-plain.json and pricey-inflated.json are identical after excluding only description and skills[].description.
- Plain: cheap picked 9 (7 deals, 2 no_deal), pricey picked 3 (1 deal, 2 no_deal). Inflated: cheap picked 4 (2 deals, 2 no_deal), pricey picked 8 (0 deals, 8 no_deal).
- All eight inflated pricey picks cite the advertised lowest-price/competitor-beating claim. The four inflated cheap reasons prefer negotiation. No selection reason explicitly identifies the inflated claim as unverified.
- Changed paths inspected remain under the student's submission directory. Logs were not edited. No API credential file was read by this review.

## Remaining review boundary

REPORT.md did not exist at the time of the initial pass; that review boundary is now closed by the follow-up below. No security scanner is configured; dedicated security scan is N/A. No reusable skill change is established by this review; managed skills were not modified.

## Final report follow-up

REPORT.md now contains all four required parts. Independently parsed its 24 episode-table rows and compared every cell to results.csv: exact agreement. Recomputed condition means (210.625, 227.5), calls (24, 25), scenario selection/deal counts, 5 cheap-to-pricey switches, 0 reverse switches, 7 unchanged choices, and 6 deal-to-no_deal pairs (3 with changed seller). Both-deal pairs are repeat 3 / s3 at 125 and repeat 3 / s4 at 330 in both conditions. All match the report. Summed original API usage: 27,523 input and 1,438 output tokens. Counted 39 protocol CHECK PASS records.

Read every inflated BUYER_CHOICE reason: all eight Juniper selections directly cite lowest-price or competitor-beating claims, zero explicitly express doubt/unverified status, and four Birch selections use negotiation reasons without that claim. The report correctly treats these as surface explanations, not internal thought or proof that the other four ignored advertising.

Checked quoted reasons and cited ranges in inflated-02 (8, 15-22, 65-74), plain-02 (63-74), plain-01 (14-19), inflated-01 (14-17), plain-03 (8), and protocol-check (21, 45, 71, 143-145). They support their associated claims. CancelTask sends only task ID; the returned task retains contextId, consistent with the table's observation of continuity.

Consulted primary course week-04.html, especially the message parameters, FP/RE discussion, and distinctions between sender beliefs/intended effects and observed outcomes. The report's ten-row FIPA/A2A table covers every required element and explicitly avoids equating task states with performatives, HTTP discovery with a central DF, and receipts with FIPA mental-state semantics. Its references distinguish smoke events from paid episodes.

The interpretation avoids attributing mixed-item mean price differences to additional spending, separates seller changes from negotiation changes, and discloses small sample, model, fixed names/endpoints, condition ordering, and non-determinism. The original MEDIUM configuration limitation is now explicitly stated as a fixed-experiment boundary; it remains a nonblocking code limitation rather than a misrepresented capability. No material report error found.

Independently ran `python3 scripts/check_week06.py submissions/25620027/week-06`: all checks passed (14 Python files, 3 cards, 4 scenarios, 12 rows per condition, 11 state-containing logs, REPORT present). This is structure verification, supplemented by the raw-evidence audit above. Final recommendation remains APPROVE with WATCH for the documented configuration limitation. No additional paid calls, server launches, source edits, or data edits were performed.
