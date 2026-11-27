# Warranty deployment (2026-11-05)

Scope: VM `market-agent`, project `studious-plate-458511-a1`, zone
`us-central1-a`. Only fast-forward `115d07f` -> `5c5298a`, then one verified
seller SIGTERM. Its existing 60-second watchdog starts the replacement.
SIGTERM can discard in-flight in-memory negotiations. Kit, in-process buyer,
tunnel, existing authentication, boot configuration and registrations remain unchanged.

## Preconditions and rollback prepared before apply

The guarded wrapper delegates to `original_watchdog(..., every)` unchanged;
the remote shared watchdog lines 1091-1102 poll and restart `procs[0]` every
60 seconds. Remote shared-kit hash differs from local but it is clean in the
remote Git checkout; the relevant remote code was inspected, not overwritten.
The guarded wrapper SHA matches local. Existing launcher/agent/seller are clean.
Offline fixture writes only unique temporary generated keys/state and blocks sockets.

If tests fail before SIGTERM: leave the old live seller intact; compare current
seller against target and backup hashes, then restore **only** `seller.py` using
`git restore --source=115d07f82589b9f70691d7d72be8ace42db5bda7 -- submissions/25512085/week-08/seller.py`.
Record the intentionally modified worktree; do not reset or rewrite history.
If the replacement fails after SIGTERM: use that same checked file-only restore
and let the existing watchdog recover. Do not repeatedly signal processes. If a
failed replacement remains alive, stop and report for renewed approval instead.

No API/secret/env/crontab/runtime-state contents are inspected or copied. Public
checks use only GET board and existing registered agent card, never transactions,
negotiation POST, registration, posting or payments. No install/new resources/push.
Local modified logs/test-report and all pre-existing untracked files are untouched.

## Evidence

`remote-before.json`, `backup-sha256.json`, `remote-applied.json`, `signal.json`,
`remote-after.json` and captured phase logs contain sanitized hashes, offline test
counts, process identities/starttimes and HTTP status. `base-files.tar` holds local
base code; remote `backup/` holds affected existing code only (no runtime state).
The initial SHA-only bundle range was rejected as empty (Git needs a named tip);
the successful retry uses `HEAD ^115d07f` without creating/changing any local ref.
Artifacts remain uncommitted for the parent session; no Git push performed.

The first apply fast-forwarded successfully but stopped before tests/signals at
an overly strict Windows-worktree SHA comparison (CRLF vs Git blob LF). Local
commit blob and remote file both hash to
`79b0f54bb024718e0ac44d41b878b2456fa029d04cdd50c7cc7e9a9e5d189dac`.
Validation was resumed against this verified blob hash; no deployed code changed
to address this tooling-only assertion and the first attempt log is retained.

## Completed result

- VM HEAD: `5c5298a9ce2f3d420e2fd166acbbd3848a026452` (fast-forward, nine files).
- VM venv `/home/comalswjd/venv/bin/python -B deploy.py validate` loaded the
  `test_warranty_disclosure_offline` and `test_grade_warranty_offline` unittest
  suites: **27 tests, zero failures/errors**. No live market test executed.
- Exactly one SIGTERM: seller PID `43684`, starttime `1037947`.
  Watchdog replacement PID `62923`, starttime `1506835`, parent shell `62921`;
  `/proc` ancestry verified back to existing kit `43679`. Seller port `52507`.
- Kit `43679` starttime `1037901` and tunnel `43696` starttime `1038186`
  unchanged; buyer remains inside that unchanged kit. No rollback required.
- Local agent card HTTP **200**; public `/api/board` HTTP **200**, `mjforge`
  **alive=true**; existing registered card HTTP **200** at the unchanged URL
  `https://combines-spelling-sam-parish.trycloudflare.com`.
- Finished `2026-11-05T10:58:23.492399+00:00`. Five remote JSON verification
  artifacts downloaded separately using gcloud SCP (single source each).
- First validation printed synthetic fixture quote/deal messages (not runtime
  logs or real transactions). Those are preserved in `validate-console.log`;
  the deployment helper now suppresses fixture output and emits statistics only.

Unverified intentionally: live negotiation, payment, inventory contents, and
in-flight negotiation preservation. SIGTERM may discard in-memory negotiations.
No code beyond the approved commit was deployed; deployment-helper changes and
records are confined to this history directory, uncommitted for the parent session.

## Coordinator verification

- At `2026-11-05T11:00:29.255Z` (20:00:29 KST), independent public GET checks
  confirmed `mjforge` seller and buyer `alive=true` and the unchanged registered
  agent-card endpoint HTTP 200. These checks do not establish live negotiation correctness.
- The local backup tar contains only directories and `agent.json`,
  `run_guarded_market.sh`, and `seller.py`; no runtime state or private-key file.
- A limited credential-pattern scan of the text deployment artifacts found no
  private-key blocks, common API-token patterns, or bearer credentials.
- This history directory is recorded in a separate local deployment-evidence
  commit. It does not change the deployed VM HEAD (`5c5298a`). No push performed.
