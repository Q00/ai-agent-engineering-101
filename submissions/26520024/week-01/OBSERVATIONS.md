# Model behavior observations

Status: not observed yet. No API key was configured during implementation,
so neither the two-tool baseline nor the three-tool agent has run on a model.
Scripted test responses must not be used as evidence of model decisions.

The reference total of the supplied synthetic runtime values is 4.5 hours.
This is an arithmetic reference, not an agent result.

After capturing real runs, record for each mode:

- The log filename and model reported in that log.
- The actual tool-call order, arguments, and errors.
- Whether the returned sum equals the reference total.
- Whether write_note ran and what was appended to the output file.
- How the two-tool run handled the unavailable write capability.
- What changed between the modes, and what cannot be concluded from one run.

Keep the goal, input, model, and loop limits identical for the comparison.
Preserve failures in their original console logs and record any later fixes.
