# TOOLS.md

The third tool is `clock`, which returns the current time as an ISO 8601 UTC
timestamp plus a unix epoch in seconds, and takes no arguments. I wrote the
description to tell the model *why* it would want to call this tool, not just
*what* it does: "call it once before and once after a task to compute how
many seconds the task took, by subtracting the two unix epoch values." A bare
description like "return the current time" would let the model call `clock`
correctly, but wouldn't tell it that calling it twice — at the start and end
of the run — is the intended pattern for self-timing, which is the actual
task in this assignment (sum notes.txt and report elapsed seconds). Returning
both an ISO string and a unix epoch is deliberate too: the epoch is what
`calculator` can subtract to get a duration, while the ISO string is what a
human reads in the log to sanity-check the epoch is right. Since the
description is the only interface the model sees, both the ordering
instruction and the dual format had to live in that one string.
