# Persistence regression evidence — 2026-10-21

Result: **8 passed**, Ruff/format clean, Basedpyright zero errors/warnings, programming skill audit zero violations. Only `tests/test_seller_persistence.py` and this evidence directory were edited by this executor. Production fixes belong to the parent executor.

## Reproduction and captured artifacts

Working directory: the `submissions/25620027/week-07` project containing this report.

Exact final invocation: `bash .omo/evidence/release-1.3.0-20261021/persistence/run-checks.sh` (exit 0).

The committed-in-evidence driver records the full runtime paths and commands. Its behavioral invocation was:

```sh
/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python -m pytest tests/test_seller_persistence.py -v -s --basetemp .omo/evidence/release-1.3.0-20261021/persistence/sqlite-scenarios --junitxml .omo/evidence/release-1.3.0-20261021/persistence/pytest-final.xml
```

Stdout/stderr: `pytest-final.log`; machine report: `pytest-final.xml` (8 tests, 0 failures, 0 errors, 0 skipped). All artifact paths below are relative to this report directory. Each scenario was invoked by the full command above, without deselection.

| Exact scenario in tests/test_seller_persistence.py | Binary observable | Captured artifact |
|---|---|---|
| `test_signed_sale_when_app_restarts_preserves_offer_and_blocks_terminal_replay` | Fresh app 2 completes app 1's taskId-only follow-up with identical task/context IDs, one receipt, exact offer 105, lamp SKU, valid Ed25519 signature. Wrong SKU and 106 acceptance are rejected. App 3 replay returns an error, and total model completions remain exactly 5. Journal restores only a tombstone. | `pytest-final.log`, `pytest-final.xml`, `sqlite-scenarios/test_signed_sale_when_app_rest0/seller.sqlite3` |
| `test_floor_and_current_ask_when_restarted_reject_invalid_model_prices` | Fresh app rejects acceptance at 99 and quote increase to 110; returns input-required without artifacts. Restored state equals SKU lamp/reserve 100/ask 105, one incoming offer; only decide is exposed after restart. | `pytest-final.log`, `pytest-final.xml`, `sqlite-scenarios/test_floor_and_current_ask_whe0/seller.sqlite3` |
| `test_task_get_list_delete_when_reopened_preserve_sdk_owner_isolation` | A new store gets/lists only each owner's tasks despite overlapping IDs. Cross-owner private IDs return None. Deleting Alice's shared ID survives another store instance and retains Bob's shared ID. | `pytest-final.log`, `pytest-final.xml`, `sqlite-scenarios/test_task_get_list_delete_when0/seller.sqlite3` |
| `test_journal_when_closed_cannot_be_reopened` | Saving a quote over a persisted terminal tombstone raises HarnessError; another restoration contains only the closed ID. | `pytest-final.log`, `pytest-final.xml`, `sqlite-scenarios/test_journal_when_closed_canno0/seller.sqlite3` |
| `test_startup_when_open_journal_corrupt_fails_closed[None]` | Normal create_app startup raises for missing open summary. | `pytest-final.log`, `pytest-final.xml`, `sqlite-scenarios/test_startup_when_open_journal0/seller.sqlite3` |
| `test_startup_when_open_journal_corrupt_fails_closed` with `{"product":{}}` | Normal create_app startup raises for malformed product schema. | `pytest-final.log`, `pytest-final.xml`, `sqlite-scenarios/test_startup_when_open_journal1/seller.sqlite3` |
| `test_startup_when_open_journal_corrupt_fails_closed` with reserve 100 and ask 99 | Normal create_app startup raises for a structurally valid summary with an invalid commercial price. | `pytest-final.log`, `pytest-final.xml`, `sqlite-scenarios/test_startup_when_open_journal2/seller.sqlite3` |
| `test_prior_memory_only_task_when_persistence_enabled_is_not_migrated` | An in-memory-only task is unknown after creating an app with a new SQLite path; JSONRPC TASK_NOT_FOUND, no additional completion, empty negotiation journal. | `pytest-final.log`, `pytest-final.xml`, `sqlite-scenarios/test_prior_memory_only_task_wh0/seller.sqlite3` |

Additional checks and exact invocations are in `run-checks.sh`: `ruff-final.log` records lint and format success; `types-final.log` records Basedpyright with the specified Python runtime; `skill-audit.log` records the mandatory Python audit. `source-sha256.txt` binds the final run to the test and production adapter files.

## Reproduced failures retained

- `pytest-first.log`: original complete 7-scenario run, 2 failed and 5 passed. Both restarted A2A flows failed with SDK context mismatch. The taskId-only request contract was retained; implementation now restores the saved owner-scoped context.
- `corrupt-semantic-first.log`: original corruption-only invocation `python -m pytest tests/test_seller_persistence.py -k corrupt -v` with the same absolute runtime as above; 1 failed and 2 passed because ask 99/reserve 100 did not raise at startup. The full 8-case suite passes after production validation was added.
- `ruff-first.log`: initial test-file formatting failures, repaired without suppressions. `types-first.log` records the initial clean type check.

## Scope and limitations

These are real SQLite files and real in-process A2A HTTP routes through `httpx2.ASGITransport`. Only `Harness.complete`, the external model seam, is scripted; stores, guards, SDK lifecycle, routes, and signature verification are real. The comparison service is disabled by environment, no lifespan network fetch is started, and the scripted completion never invokes the model API. This executor made no external API calls, deployments, SSH calls, or Git changes.

Restart means newly constructed app/store instances using the same SQLite path, not an OS process kill or overlapping multi-process rollout. The old-memory case exercises the current app's nonpersistent mode; it does not execute a historical 1.2.1 binary or demonstrate migration of that runtime's prior in-memory tasks. **No zero-downtime or old-task migration claim is made.**

Skill reflection: the programming skill was applied and its Python audit passed. No reusable skill change was identified; the plugin-managed skill was not modified.
