# 2026-11-10 16:00 KST: `git commit -a` swept unrelated week-07 logs into a commit (caught by the hub)

- What: commit a6208b0 (fixing a test literal) was made with `git commit -qam`. `-a` also staged the 14 week-07
  log files that had been modified-but-uncommitted for weeks (one of them 733 MB) and logs/deploy/deploy.jsonl.
- Caught: the deploy timer tried to push the server commits to the hub; hub_pre_receive.py refused it
  ("adds .../week-07/logs/buyer-resilience-20261022T160304Z/runtime-console.log (733 MB > 50 MB)"), twice
  (deploy.jsonl 15:53, 15:56). A clone pushed 39aaf9b meanwhile, so server and hub diverged (15:59).
- Fix: a6208b0 had never left this machine (no remote contained it), so it was undone with `git reset --mixed`
  (working tree untouched: the week-07 files are back to modified-uncommitted, exactly as before), the test fix
  was re-committed with an explicit path (83f077b), and hub/week-08 was merged in (83f6cb9), no force push.
- Rule from now on: commit with explicit paths (`git commit -- <paths>`), never `-a`, on the server checkout:
  it carries long-standing uncommitted changes that are not ours to commit.
