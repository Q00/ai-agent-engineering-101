# Ouroboros self-evolution delivery checklist

Final outcome: structured market evidence automatically triggers an isolated
Ouroboros code candidate, verifies it, canary-deploys exactly one live role,
and promotes or rolls it back without human intervention.

Status meanings: `[x]` verified, `[~]` implemented but not yet verified in the
complete path, `[ ]` not complete.  A component existing by itself is not
enough to mark the end-to-end pipeline complete.

## A. Evidence and trigger

- [x] Official rules, agent contract, board and online shops are polled every 30 seconds.
- [x] Rank evidence records Class Player score/rank and 1/3/5/10 cutoffs.
- [x] Evolution input contains bounded structured counts/hashes only; raw forum/log text and secrets are excluded.
- [x] A 30-minute review triggers when the Top-10 gap does not close; rule changes and runtime failures bypass the timer.
- [x] The lower official Buyer/Shop component selects exactly one target role.
- [x] Overnight baseline and wake-up comparison are stored as official evidence; no improvement rejects the incumbent hypothesis.

## B. Candidate generation isolation

- [x] A single durable evolution lock prevents concurrent code-generation cycles.
- [x] The daemon invokes pinned Ouroboros with a generated target-role Seed.
- [x] Candidate edits occur only in a detached temporary Git worktree.
- [~] Candidate generation has a 30-minute hard timeout with process-group TERM/KILL cleanup.
- [~] A crash/restart resumes from durable cycle state without launching a duplicate candidate.

## C. Validation and trust boundary

- [~] Candidate paths are restricted to this submission and the selected role.
- [~] Dirty overlap, path escape, `.market`, logs, keys, env and secret patterns quarantine the candidate.
- [x] External/forum text never reaches the generated coding prompt.
- [~] Syntax/compile and the complete local unittest suite pass in the candidate worktree.
- [~] A failed validation leaves live code and live processes unchanged.

## D. Deployment, canary and rollback

- [~] Applying a candidate is atomic and committed as a new grading-history commit.
- [x] Supervisor receives an atomic request and drains/checkpoints/restarts only the selected role.
- [~] Buyer canary requires 20 completed orders and at least 10 minutes.
- [~] Seller canary requires 10 quotes; safety/runtime failure aborts the canary.
- [~] New UNCERTAIN mutations, role exit, timeout or failed restart automatically reverts the candidate commit and restarts the prior role.
- [x] Every cycle and state transition is stored in append-only control evidence.

## E. Continuous operation proof

- [ ] Unit tests for trigger, isolation, restart request, validation, deployment and rollback all pass.
- [x] Evolution is owned by the background supervisor and survives observer/terminal closure.
- [x] A harmless fake candidate completes the full CREATED→VALIDATED→CANARY→ACTIVE path.
- [x] A deliberately failing fake candidate completes the full CREATED→VALIDATED→CANARY→ROLLED_BACK path.
- [x] The first real evidence-triggered cycle starts automatically without a manual command.
- [x] Windows logon recovery restores supervisor plus evolution daemon as a single-owner process tree.
- [x] Morning report compares official baseline/current rank, score and gaps and identifies the deployed commit/cycle or the exact no-deploy reason.

The pipeline is complete only when every item in section E is checked and no
`[~]` remains in sections A-D.
