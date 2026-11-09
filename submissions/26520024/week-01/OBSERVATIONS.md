# Model behavior observations

The following results come from two real runs through the authenticated Codex
CLI, not the scripted offline tests.

## Shared setup

- Codex CLI 0.153.0, ChatGPT authentication, model gpt-6-astra, low reasoning.
- Python 3.12.10, maximum 8 decisions, 120-second timeout per decision.
- Same DEFAULT_GOAL, model instructions, notes.txt, and loop in both modes.
- Synthetic input: baseline_run 1.25, adapter_run 2.50, evaluation_run 0.75 hours.
- Independently calculated reference: 1.25 + 2.50 + 0.75 = 4.5 hours.
- Only the offered tool set and corresponding allowed decision names changed.
- Python executed the tools. Codex internal tools were disabled; the event
  streams contained no internal tool actions.

## Observed comparison

| Item | Two tools | Three tools |
|---|---|---|
| Full log | [codex-model-2-01.log](logs/codex-model-2-01.log) | [codex-model-3-01.log](logs/codex-model-3-01.log) |
| Tool-call order | read_file -> calculator | read_file -> calculator -> write_note |
| Total model decisions, including final answer | 3 | 4 |
| Calculator expression | 1.25 + 2.50 + 0.75 | 1.25 + 2.50 + 0.75 |
| Calculator result | 4.5 | 4.5 |
| Final reported total | 4.5 hours | 4.5 hours |
| File-writing action | None available | write_note succeeded |
| Saved report | No file after this run | outputs/gpu-summary.txt |
| Python tool errors | 0 | 0 |
| Process exit status | 0 | 0 |

In the two-tool run, the final answer listed all input values and explicitly
said it could not save the report because a writing tool was unavailable.
The absence of outputs/gpu-summary.txt was checked before the three-tool run.

In the three-tool run, the model requested write_note after observing the
calculator result. Its content argument included all three values, the total,
and a statement that these were demonstration data. The saved file was then
read and checked against the write_note argument:

    Sample GPU runtime entries (demonstration data, not actual experiments).
    baseline_run: 1.25 hours
    adapter_run: 2.50 hours
    evaluation_run: 0.75 hours
    Total GPU runtime: 4.5 hours

Both logs retain nonfatal Codex state-database warnings in stderr. These were
CLI warnings, not failed Python tools or failed model decisions.

## Interpretation and limits

Adding write_note enabled the persistence part of this task. The model kept
the same read-and-calculate prefix and added a writing action before its final
answer; the sum itself did not change.

This is one run per mode on a simple synthetic input. It does not establish a
general success rate. Both modes were instructed to use read_file for file
contents and calculator for arithmetic, so the observation is conditional on
that policy. The description wording was not varied independently; this
comparison cannot attribute success to a particular phrase in TOOLS.md.

Every decision used a fresh Codex process with the accumulated history.
This is a Codex CLI-backed decision loop, not a direct Anthropic or OpenAI SDK
function-calling run. The Python loop and three exposed functions remain the
subject of the experiment.
