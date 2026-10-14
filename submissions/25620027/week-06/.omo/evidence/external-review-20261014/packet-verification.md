# Review packet preparation verification

Scenario: prepare two review artifacts without altering the 87 selected original source/evidence files. This check validates packet preparation, not the assignment's execution results.

Invocation: `python3 - <<'PY'` from repository root; Python pathlib reads REQUEST.md and REVIEW_PACKET.md, extracts the 87 source manifest rows, recalculates SHA-256 and byte lengths for each original path, asserts artifact sizes below 400 KiB and scans for OpenAI/Anthropic/Google/GitHub key, Bearer token and private-key signatures; `git rev-parse HEAD` records revision.

Binary observable: process exit_code=0; assertions completed. Captured exec chunk_id: 2ddcf3.

Actual command output:

```text
PASS: source snapshot SHA-256 and byte counts match 87/87 current original files
{"file": "REQUEST.md", "bytes": 4826, "sha256": "733c92eb668476150ea53f18a37ee8b07e4964424ee4b07abb21647a2c01f42c", "secret_pattern_matches": 0}
{"file": "REVIEW_PACKET.md", "bytes": 359705, "sha256": "e62b3237461d38e8dfb6428b33688c1a0c7f6ab82c4857584248514239d91555", "secret_pattern_matches": 0}
PASS: both requested artifacts exist, nonempty, below 400KiB, secret-pattern scan zero
HEAD=23351c55775ae520ee012507285f61d10409de90
```

Judgment: PASS for packet existence, bounded size, manifest consistency and stated secret-pattern check. The scan cannot establish absence of every possible secret. External reviewer opinions and independent execution are outside this helper's task and remain root-owned.

## Supplemental raw sources verification

Invocation: Python3 read SUPPLEMENT.md; assert 0 < bytes < 102400; recompute SHA256 of http_client.py, canonical trade-test.log and buyer-under-test-buyer_holds_budget-1.log plus `git show 42f05e308c16f260672777fdbcc1d0a248fdf6aa:submissions/25620027/week-05/market/host.py`; assert each digest occurs in the supplement.

Actual process exit_code: 0. Captured command output:

```text
PASS: supplement exists and is below 100 KiB; all four source SHA256 values match live files/Git object
SUPPLEMENT bytes=44114
SUPPLEMENT SHA256=04734ba378f01a8c5b2f7da4f5abb0614bfe3214e04e750c751cbe4fa5268815
```

Judgment: supplemental artifact creation and four source hash references verified. No fresh assignment execution performed.

