# Isolated official market test

The unchanged current official test ran on 2026-10-25. Its normalized SHA256
was d610bc15448f5960d0b9b6bb9f181b38829bfb2ee863ab6c2a96abb9cb78f04d.

Result: 10 of 25 checks passed; the overall result is FAIL.
The first two lamp negotiations opened and sold at 115. Their receipt signatures
verified. Subsequent harness calls failed with the CLI diagnostic "You've hit
your usage limit". The diagnostic suggested trying again at 9:22 PM; this is
the time reported by the CLI, not an independently verified reset guarantee.

The buyer could not choose a shop because its harness calls failed. Its test
logs therefore contain no completed output line or A2A negotiation. Reserve
checks marked PASS after seller errors are not evidence of healthy negotiation.
The completed run does not establish that the updated agents pass the test or
that the live model makes correct adaptive pricing judgments.

Previous reports and logs are copied unchanged under `before/`. This run's
simulated seller data is under `seller-data/`; its separate buyer-memory path
was configured but no buyer trade reached the append step. `finish.json`
confirms that all three live memory fingerprints stayed unchanged.

Next: after model access is available again, rerun `run_isolated_market_test.py`.
Keep this failed attempt and the new result. Do not replace model decisions with
stub responses to produce a passing official report.
