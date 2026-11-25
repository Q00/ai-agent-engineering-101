# Seller cooperative drain

Only the trusted local runtime owner may write `.market/seller.drain` with the current seller PID. No network control endpoint is added. Keep the directory private. PID matching is instance scoping, not authentication; PID reuse is possible, so remove obsolete markers before startup.

Future procedure: verify current seller PID and command, run `.venv/bin/python seller_drain_wait.py --pid PID --timeout 1200` from week-08 as runtime owner. This helper never kills a process. It requires a fresh status timestamp after this request, matching PID, flush, quiet=true, all counters zero. Timeout means abort replacement, not force kill; remove marker to resume admission. Recheck PID identity and fresh quiet immediately before separately authorized TERM, then verify replacement PID, agent-card and new quote. Do not restart kit/buyer/inventory.

New tasks are rejected while draining; existing Seller.open tasks continue. Stale expired-hold talks are counted separately and block quiet; SDK nonterminal metadata is not exhaustively enumerated. late waits also block quiet. Counters are recaptured after writer flush yields. Status is advisory at a point in time, not a cross-process transaction. A future continuation can still arrive after status publication; no zero-loss guarantee or durable migration is claimed. Preserve live logs and report actual loss/unknowns.

The initial deployment from an older process has no drain support. Its one-time explicit loss approval is distinct from the future procedure; never fabricate a zero census from quiet logs. New 3% policy rollback uses seller_opening_default.disabled for new conversations only; it cannot restore lost process memory.
