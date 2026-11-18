# Preserved concurrent-runner failure

This directory contains an invalid experiment attempt preserved as process
evidence. It is not part of the final 24-episode result and is excluded from
every aggregate in the top-level `REPORT.md`.

Two runner processes briefly operated on the same output paths. The preserved
`results.csv` therefore contains duplicate `prompt_inject` run 2 keys for S3
and S4, plus a crash record reporting that the model used a different
`negotiation_id`. The interleaving is visible directly in
`logs/prompt_inject-02.log`; for example, lines 225–228 switch from negotiation
ID `3LFRX92KXhIKDJQT52ZqCIDb` to `kEjUHlQlz_P2qUrGO-iqCnll` within one logged
sequence.

The attempt was stopped rather than repaired in place. Its CSV, two logs, and
authentication output are retained unmodified. A new run was then started
from an empty scratch output directory only after confirming one market server
and zero runner processes twice. That clean run produced the final top-level
submission artifacts.
