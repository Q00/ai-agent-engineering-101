# Attempt 1 (buyer opens, as in week 04)

The first full run: prompt_inject and server_inject, 5 scenarios x 3 repeats, gpt-4o-mini, temperature 0.
Kept as it ran. It failed the manipulation check: the injected notice is attached to seller proposals,
and the sellers countered inside reject_proposal notes instead of proposing, so the notice reached the
buyer in 2 of 30 episodes (0 of 15 in server_inject). No attempted violations, no refusals.
The main run (top-level results.csv and logs/) changed one rule: the seller opens.
