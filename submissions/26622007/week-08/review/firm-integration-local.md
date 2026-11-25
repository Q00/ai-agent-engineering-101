# Firm follow-up local integration

Status: local source implementation, default OFF. Operations 0, real A/B 0,
deployment/push/SSH 0. Parent independent review and deployment gate remain
pending. Full 857 suite HOLD and full-main callback E2E unverified remain separate.

## Scope and root cause

Base 4d1a0a8; prior independent pure review reported no findings and PASS.
Parent receipt 200151-firm-pure-adjacent confirms 69 tests on clean base using
the specified Python 3.13.17 with httpx. This is not the rejected full-suite test.
The 1925 investigation identified 11 strict firm follow-up messages across five
cancelled talks where the original ladder returned None. Economic effect 0.
Three multiline/date messages and later audit variants are excluded.

Changed files (all within this student's week-08): buyer_firm_trial.py,
market_buyer.py, test_buyer_firm_trial.py, logs/firm-integration-local-tests.txt,
and this report. Pure buyer_firm_followup.py remains byte-unchanged.

The existing price_ladder still computes opening/discount/first-one decisions
first. The trial adapter immediately returns any non-None baseline unchanged.
Only a missing decision can reach the pure helper. No provider/model/network
call was added to the callback. buyer.py, oneshot.py, ask_read.py, price caps,
hard_limits, seller/restock and quote alignment source/policy remain unchanged.
No assignment journal, lock, worker, fsync or new file IO was added to production.

## Eligibility and policy contract

ENABLED=False, START=0, END=START+1800, static ASCII STUDY. Zero start is off.
When eventually configured by the parent gate, current time and the existing
order's _buyer_started_at must both be within [START, END), with started <= now.
The recorded start is the existing buyer invocation/order start, not a newly
invented market creation timestamp. Old in-memory order retries are excluded;
the kit must preserve the original order start when resuming an old order in a
new process if that ever becomes a supported execution mode. That execution
mode is unverified here and is a deployment gate precondition.
END stops further exposure, including additional callbacks on assigned orders.
OFF restores baseline immediately; it never cancels an already sent price.

Goods only, baseline None, metadata None, exact fullmatch standing ask,
complete valid history and a nonempty pure B result are required. Probe B's
pure result in BOTH arms before assignment: qualification depends solely on
pre-treatment inputs, not a treatment outcome. Invalid/no-result inputs do not
enter the cohort. The study/order SHA256 hash fixes the arm across retry/new
task, with probability 1:1; exact sample balance is not guaranteed.

policy_ok is computed from real current refusal checks: quoted.refused absent,
quote_refused on the retained quote, goods_text_refused on quote and current
message, plus integer positive cap <= integer budget. The pure helper checks
every historical offer against cap/ask. These are negotiation-policy checks,
NOT warranty certification. An absent warranty statement is not proof of a
warranty; the exact firm phrase cannot supply such proof. The existing request,
stop_if, receipt check, warranty_short, over_limit, accept policy and kit's final
verification/payment authority are retained. Receipt warranty 0 still fails;
6-hour warranty still meets the existing requirement. No parser result certifies
a receipt or promises payment. If the deployment gate requires affirmative
quote warranty proof beyond these existing checks, integration activation is
HOLD until the caller contract is clarified; do not set policy_ok unconditionally.

## Measurement boundary

Before selecting the actual A/B decision, existing buyer.log emits and flushes:
`[firm-assignment] ` followed by one fixed JSON object with only study, order,
arm, time, shop, sku. The existing timestamp/buyer prefix is retained. No body,
address, key or receipt is logged by this event. Safe ASCII identifier grammar
also prevents control characters and addresses in these identifier fields.
The B pure eligibility probe happens before this event; actual treatment
selection happens after it. Logging failure cannot proceed to a B decision.

`[firm-matched]` records first-match flag, baseline None, candidate proposal,
actual selected decision and history index. `[firm-history]` after talk and
order_record.firm_trial record study/arm/assignment time, eligible callback count,
actual code-decision count and observed history offer separately. A's original
decision is None at the callback; its later harness offer is observed from
history, not mislabelled as a code decision. Counts are per callback, not ITT
order counts. history_offer means buyer.talk's offer attempt; a failed send can
leave it in history, so it is NOT confirmed delivery or payment.

The reader must extract tagged JSON from the existing kit-captured stderr log,
deduplicate assignments by (study, order), keep the earliest actual assignment
time, and preserve all assigned orders in ITT even without terminal measure.
Repeated task/process assignment lines are allowed; the same key has the same
arm, not additional cohort entries. Never reconstruct assignment time from
order completion. Left-join terminal orders/payment/settle on order; missing
terminal remains unknown/pending, not silently dropped or paid=false. Compare
paid/ITT, settled/ITT, realized saved sum/ITT and total calls/ITT. No actual
capture-reader/payment join was run here; fake strings only. Parent must verify
kit capture completeness and original-start handling before activation.

## Verification and review limits

Approved seams: pure trial/helper and actual nested price_ladder + buyer.talk
with fake send/RPC/decide. The callback is loaded from its actual AST with
fixture closure inputs, without executing main. Top-level import audit found
constants/regex/locks and environment configuration reads; market HTTP and
subprocess actions are inside functions. Test import does not execute main.
No key/environment values were inspected. No live market API was called.

mp-tdd SKILL/tests/mocking used: initial new module import RED, then off-seam
GREEN. Later boundary/fake additions were not independently demonstrated RED;
do not claim every case had a red-green cycle. Precommit approved run: 79 tests
PASS (10 new trial/fake + 69 prior helper/oneshot/ask_read), 0.879 seconds.
Original console output is preserved in the new logs file without editing.
Cases include before/start/end, old start, future start, invalid clock,
zero-start, off, stable A/B retry, pre-treatment qualification, cap 1, first
decision, positive B offer, B walk, current quote refusal, no-warranty stop,
terminal task and identical off/A output history. Existing metadata/take/reply/
guard regression tests are retained. Buyer/receipt source is unchanged.

Original condition: real market negotiations, task metadata and economic
settlement. Tested condition: synthetic callback closure + real buyer.talk with
fake external boundaries, pure helper and adjacent tests. Original real-market
condition was not reproduced. No bind/nested/full857 retry, exclusion changes,
permission bypass or security-source-capability retry was made.

Postcommit official labels: firm-integration-pure, firm-integration-fake,
firm-integration-diff, firm-integration-syntax, followed by check requiring all
four. Use only the previously authorized VERIFY_RECEIPT_ROOT
`/tmp/ax-quote-owner-receipts`; final tool receipts are the authority, not this
prewritten command list. The external result report records the actual outcome.

Manual full change review covered trust boundaries, baseline preservation,
arm timing, logging fields and policy/receipt separation. No confirmed defects
remain in this local candidate. Independent review is deliberately deferred to
the parent per the single-owner/no-new-coder instruction. Formal security
workflow is unassessed and was not retried. gstack investigate/review and
central work-review were read; automated gstack workflow completion is not
claimed. Incident lesson applied: receipt/kit completion is not settled success.

## Corrections and rollback

|Constraint|Observed implementation/result|
|---|---|
|pure preparation versus integration|Actual callback + trial added; live activation 0|
|first quote effect|First decision preserved; only follow-up None is eligible|
|11 opportunities versus economic gain|Five historical cancellations; gain remains 0|
|date/new audit variants|Excluded by unchanged exact pure helper|
|policy verdict versus warranty proof|Distinct; final receipt/kit checks retained|
|quote alignment and full857|Source unchanged; operation/full-suite HOLD retained|
|source trial activation|False/zero start; future window needs parent gate|

Rollback operational procedure (design only, no operation performed): set
ENABLED=False in a reviewed complete source bundle; stop admitting new orders,
allow already active negotiations to drain without withdrawing prices, verify
active count zero, then atomically swap the complete prior reviewed bundle.
For multi-file changes use a versioned directory/pointer swap; do not edit a
live module in place. Restart/switch timing needs parent approval and capture
checks. Local history-preserving recovery uses git revert in reverse commit
order; never reset/squash. No production process was changed in this task.

DEBUG REPORT
Symptom: strict firm follow-ups fall through to harness.
Root cause: existing ladder returns None for this exact repeated fullmatch phrase.
Fix: default-OFF, order-hashed eligible fallback in callback.
Evidence: clean-base adjacent receipt and 79 approved local precommit tests.
Regression: actual callback AST + buyer.talk fake; final receipt commands above.
Status: local candidate only; independent/deploy/economic gates pending.

리뷰 결과: 위험도 저(기본 OFF 로컬 후보) · 확인된 지적 0건(Important 0 / Nit 0 / Pre-existing 0) · 로컬 고칠 것 없음 · 독립리뷰 대기
검증: 허용 pure/fake·인접 79 PASS; 최종 receipt 결과는 외부 결과 보고 참조. 전체857 HOLD·실 main E2E·실시장 미검증
보안: 수동 검토 지적0; 정식 security 미평가·source cap 재시도0
정정 대조: 위 7행 일치; 원래 시장·지급 조건 미재현 보존
되돌리는 법: OFF → 신규중지·drain → 완성된 이전 bundle 원자 복구; 로컬 역순 revert, 운영실행0
