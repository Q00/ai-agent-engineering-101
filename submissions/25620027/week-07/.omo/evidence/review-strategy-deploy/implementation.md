# Seller strategy implementation

UTC recorded: 2026-10-20T15:22:39.114893+00:00

## Scope

Changed only seller_policy.py, seller_turn.py, tests/test_opening_ask.py, tests/test_signed_seller.py and new tests/test_seller_settlement.py. Existing runtime, keys, agent registration, source logs, buyer code, card and advertisement were not touched. No API, official 25-check run, commit or process restart executed by this worker. Parent owns final review and deployment.

## Contract

Opening ceiling is integer ceil(reserve*1.10): basic lamp110/chair198/bookshelf286. Model chooses opening price inside guards. A feasible actual offer must settle if at least95% of current ask (including offers above ask), or if it is the second or later incoming numeric offer. The model still calls accept with the exact actual offer; quote/reject receive structured correction feedback instead of mutating the task. Existing reserve, actual-offer, selected-SKU and signed-receipt checks remain.

Numeric incoming_offers is independent of rounds (quote count), increments once per numeric buyer message and never per model retry or nonnumeric message. It counts a below-reserve numeric bid as an incoming offer, but settlement always additionally requires reserve feasibility. Coupon credit=min(5,total_reduction) on the first downward quote; larger negotiated reductions are allowed. total_reduction and coupon_discount are separate wire metadata. Existing coupon-used state persists. No rating condition is introduced.

Opening text remains available for catalog search. Once a product is selected, buyer_text is null in model state and task_id is omitted. The numeric offer, selected catalog product, code-computed settlement flag, numeric counts and prior own-price facts remain available.

## Baseline findings and legitimate test changes

Before source changes, the two existing seller test modules produced9failed/2passed, preserved in baseline-tests.txt. Opening tests still expected the old5% range while deployed source used20%. Updated cases now assert the requested10% contract (including reserve101 -> ceiling112). The old signed-receipt fixture attempted a later quote120 after opening105, which violates the already-existing monotonic guard. Changed only that later quote to105; the actual subsequent offer120, receipt120, Ed25519 verification and replay rejection assertions remain unchanged.

## Verification

All commands run from the Week07 submission with cached tooling at /Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/.

- `python -m pytest tests/test_opening_ask.py tests/test_signed_seller.py tests/test_seller_settlement.py -q`: exit0, **31 passed**; captured seller-tests.txt.
- `ruff check market_a2a/seller_policy.py market_a2a/seller_turn.py tests/test_opening_ask.py tests/test_signed_seller.py tests/test_seller_settlement.py`: exit0; captured ruff.txt.
- `basedpyright --pythonpath <cached-python> <the same five files>`: exit0,0errors/0warnings/0notes; captured types.txt.

Scenarios: integer opening-ceiling and below-reserve correction, invalid opening state/event nonmutation, exact95% boundary (110/104 versus105 and120/113 versus114), second-offer acceptance, impossible/absent offers never forcing settlement, quote/reject delay refused without events, larger18-unit negotiated reduction with5-unit coupon part, smaller3-unit coupon, later15-unit ordinary reduction without reusing coupon, actual-offer and SKU mismatch blocked, below-reserve accept blocked, and real in-process ASGI sequence proving nonnumeric/retry counts and exact final100 receipt. Signed and unsigned real A2A route scenarios retain high actual120 offer, exact receipt, signature verification and terminal replay rejection.

Only the external model-completion seam is scripted; protocol route, tool loop, guards, events and signer execute normally. All test transport is in-process; no live purchase or API behavior is claimed. Official25 and deployment are parent-owned pending work.

## Source hashes
- market_a2a/seller_policy.py: da54a5f2bc7bb7e19a8ce53fa229ba909b6e2922a1ca6109c3812ee5234ddc35
- market_a2a/seller_turn.py: 78a933b212ae074845899f390261840a0a501bcd89cdbdeccbceb061f0898a5a
- tests/test_opening_ask.py: 9d545e5a62b69af7c29af3a2ce059ef801d13e7d75d8a47b2045b31dc0b3a577
- tests/test_signed_seller.py: 4ed29c7eb3645c63ffcf97d4d76c0076207105a96f26286d44a2c7e324c8d083
- tests/test_seller_settlement.py: 2548a4b8ed572d8ab5b71ef9a53ddc9eacfdbd2e179a96dc2513362592c13a6f
