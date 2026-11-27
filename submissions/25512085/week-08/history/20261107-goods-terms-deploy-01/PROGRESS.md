# VM goods terms candidate, not deployed

Read AGENTS.md, current status/history, local REPORT and correction commits,
previous integer-wire deployment helpers (read only). No week-08 README/checker
exists locally. User dirty files and prior evidence remain untouched.

Live inspection: HEAD 4a5deb685368343abfd1310c572288a924351b01,
expected buyer SHA256 matched. Seller baseline byte/AST equals 92729a6 parent.
Local facade/internal pricing+risk functions are AST equivalent to remote
monolith after removing injected facade parameter. Wrappers, arguments, config,
and PRIOR_BREAK checked. No facade refactor is transferred.

First prepare failed on Windows CRLF versus committed LF. Original failure
retained in attempt-prepare-01.log and dependency-initial.json. Revised prepare
uses committed LF and verifies normalized worktree equality. Successful manifest
and two-file patch generated. Production seller/test are not touched remotely.

Safety blocker: seller.open and InMemoryTaskStore are process-memory-only.
Read-only kit source shows watchdog only restarts an exited seller; it does not
gate new incoming negotiations or drain pending seller tasks. Seller exposes no
pending-count/drain endpoint. Killing seller may erase active commitments even
with graceful HTTP shutdown. No signals or live source replacements are allowed
until safe pending-state determination exists. Offline candidate verification
continues in unique source-only remote history; live activation is on hold.
