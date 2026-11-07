# Isolated official market test: PASS

The unchanged current course test completed with all 25 checks passing.
All three seller lamp deals opened and sold at 115 with valid signatures.
All three seller bookshelf negotiations countered at reserve 260 and rejected
another below-reserve offer. Buyers stayed within budget and passed A2A protocol
checks. The review-choice test picked the well-reviewed shop in all three runs.

`finish.json` confirms live seller pricing state, seller history, and buyer memory
were unchanged. Simulated test data stays in this run's separate memory paths.
`before/` preserves the preceding failed report and its logs without editing them.

This is a trade/protocol test with simulated counterparts and real harness calls.
It does not establish improved live market share or average price, identify a
dishonest participant actually encountered in the live market, or exercise a
six-result adaptive review: the seller completed only three negotiations per
item. Those assignment requirements need their own evidence.
