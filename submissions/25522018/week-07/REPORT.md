# Week 07 — Agent Market: Reputation and Verification

Student ID: **25522018**. Provider: GroqCloud, model: `openai/gpt-oss-20b`.

## Design

This solution builds on the Week 06 A2A buyer and seller. The market kit runs the existing buyer for Round 0. For Round 1, `market_buyer.py` reads registry shops and public reviews, ranks sellers by a smoothed reputation score that also incorporates first-hand memory, negotiates through the existing A2A client, and writes local memory keyed by shop name (not URL). In Round 2, the shop signs `{shop,item,price,task}` using Ed25519 over canonical UTF-8 JSON. Keys and runtime memory are excluded from Git. The buyer and seller still use GroqCloud for their negotiation decisions; code enforces reserve and budget constraints.

## Actual local tests

**Not yet run in this provided package.** Run `scripts/trade_test_week06.py` and `scripts/market_test_week07.py` on Windows, then replace this paragraph with actual results from `test-report.json` and `logs/market-test.log`. Report any failing tests, corrections and reruns. Do not claim 25/25 before observing it.

## Three live rounds

- Round 0: [fill in actual registration and order outcomes, including the shop assigned by the kit]
- Round 1: [fill in actual reputation-based shop choice, memory and reviews]
- Round 2: [fill in actual signature verification and accepted or rejected reviews]

**Class market URL is provided by the instructor.** No real market results are included in this starter package.

## Discussion and limitations

Reputation uses self-reported reviews in Round 1; negative or fabricated reviews can distort selection. Signed receipts in Round 2 bind a registered shop key to the item, price and task ID, but do not establish real-world delivery quality or customer satisfaction. A new shop has little evidence, so Bayesian smoothing and a neutral prior avoid assuming its quality is zero. Signature verification is only as trustworthy as the key registration, task binding and the market's duplicate-order checks. Persistent memories are local and do not imply that the review system itself is free from strategic manipulation.
