# Why `write_note` is described the way it is

The third tool is `write_note`, and the name was kept deliberately even though it
works against the tool. In nearly every language and library a model has read,
`write` means truncate-and-write: `open(path, 'w')`, `fs.writeFile`,
`File.WriteAllText`. This tool appends. So the name emits one signal and the
behaviour is the opposite, which means the description has to do the work of
overriding a prior rather than merely labelling a function. That collision was
the point of the exercise, so instead of renaming to `append_note` I started from
the naive description — `"Write a note to a file."` — and let it fail. It failed
exactly along the fault line: given "read notes.txt, sum the numbers, and record
the total back into notes.txt", the model chose the right tool in the right order
but passed the **entire original file plus the new total** as `text`, because it
expected truncation and was helpfully reconstructing what it thought it was about
to destroy. The file went from 8 lines to 18, with the memo duplicated
(`logs/run-01-thin.txt`). The instructive part is that the failure was not in
tool *selection* — it was in the *argument* — so the repair could not just be a
better verb. The current description therefore leads with the surprising fact
("Appends a single line to the end of a text file"), states the consequence for
existing content affirmatively before denying the alternative ("Everything
already in the file is preserved: the file is never truncated and existing lines
are never replaced"), and then constrains the payload directly ("Pass only the
new line in 'text' — do not resend content that is already in the file"), with
that same constraint repeated in the per-parameter description where the model is
actually filling the slot. Affirmation before negation is intentional: a bare
"does not overwrite" leans on negation, which models handle unevenly. After the
change the model passed `'Total: 69504'` alone on 3 of 3 runs
(`logs/run-02-explicit.txt`).

One thing a description provably cannot fix showed up next. Told to "replace the
entire contents of notes.txt, discarding everything else", the model correctly
sent only the new line — the prior stayed suppressed — but then reported "Done.
The file now contains only `69504`" while the file held 9 lines
(`logs/run-03-adversarial.txt`). The description had governed what the model
*sent* without telling it what had *happened*, and the tool's return value was a
bare `"ok"`, which teaches nothing. Returning a factual observation instead
(`"appended 1 line; notes.txt now has 9 lines and nothing was removed"`) changed
the final answer to an admission: it needs "a file writing tool that supports
truncation, which isn't available in my current tool set"
(`logs/run-04-adversarial-v3.txt`), with no regression on the normal task
(`logs/run-05-record-v3.txt`). So the interface is not the verb alone. It is the
description, the per-parameter descriptions, and the return value together — the
description shapes the call, and the return value is the only channel that can
correct the model's account of what it just did. I left the overwrite flag out for
the same reason: no task here needs truncation, and exposing a destructive path
the work does not require only adds a way for the loop to go wrong.

Unrelated to the wording but worth recording: every run summed `Attendees: 4`
into the money total (69504). The tool descriptions are not at fault — `notes.txt`
says to add "the numbers above" — but it is a clean example of an agent
faithfully executing an underspecified instruction.

## Disclosure

Per the course LLM policy, the wording above and the three description/return
revisions were drafted and iterated with Claude Code (Opus 5). Each attempt,
including the deliberate failure, is a separate commit with its run log; nothing
was squashed.
