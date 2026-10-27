# Week 07 — Agent Market: Reputation and Verification

Student ID: **25522018**. Provider: GroqCloud, model: `openai/gpt-oss-20b`.

## Design

This solution builds on the Week 06 A2A buyer and seller. The market kit runs the existing buyer for Round 0. For Round 1, `market_buyer.py` reads registry shops and public reviews, ranks sellers by a smoothed reputation score that also incorporates first-hand memory, negotiates through the existing A2A client, and writes local memory keyed by shop name (not URL). In Round 2, the shop signs `{shop,item,price,task}` using Ed25519 over canonical UTF-8 JSON. Keys and runtime memory are excluded from Git. The buyer and seller still use GroqCloud for their negotiation decisions; code enforces reserve and budget constraints.

## Actual local tests

The official tests were executed on Windows using GroqCloud (`openai/gpt-oss-20b`).

- Week 6 A2A compatibility: **21/21 passed**
- Week 7 market test: **25/25 passed**
- Ed25519 receipt signatures: verified in 3/3 seller-deal tests
- Reputation-based shop selection: selected the positively reviewed seller in 3/3 runs
- Buyer budget enforcement and A2A task continuation: all relevant checks passed

Evidence is provided in `test-report.json`, `logs/trade-test.log`, and `logs/market-test.log`.

The live class-market rounds have not yet been completed.

## Three live rounds

The agents joined the instructor's shared market under GitHub account `@raksmeyphann`.

- **Round 0:** Historical outcomes have not been separately verified in this report.
- **Round 1:** The local reputation-selection tests passed 3/3; historical live-round outcomes have not been separately verified.
- **Round 2:** The official market checker confirmed that both the shop and buyer were running. At validation, the shop had 89 counted deals and the buyer had 48 counted deals.

The live-market checker completed successfully with `all checks passed`. Evidence is recorded in `logs/market-kit.log`.

Intermittent GroqCloud upstream errors occurred during live participation, resulting in retry attempts and some failed decisions. Despite these errors, the market checker confirmed successful participation.

## Discussion and limitations

Reputation uses self-reported reviews in Round 1; negative or fabricated reviews can distort selection. Signed receipts in Round 2 bind a registered shop key to the item, price and task ID, but do not establish real-world delivery quality or customer satisfaction. A new shop has little evidence, so Bayesian smoothing and a neutral prior avoid assuming its quality is zero. Signature verification is only as trustworthy as the key registration, task binding and the market's duplicate-order checks. Persistent memories are local and do not imply that the review system itself is free from strategic manipulation.
