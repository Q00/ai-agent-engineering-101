# Week 11: Round 3 closes

**Due: Monday, November 23, 23:59.** The deadline is judged by the PR open timestamp.

Lecture notes: https://wpti.dev/ai-agent-engineering-101/week-11-en.html

## Round 3

Round 3 closes on November 17 at 23:59 KST. The board at that moment is saved, and the ranking part of the midterm
comes from the Class view of its Players table. Settlements after 23:59 do not count. Keep your agent running until then.

## Finish the midterm

Put the week 8 deliverables in `submissions/<student-id>/week-08/`:

- `logs/market-kit.log` from the machine that ran your agent, with its settlement lines,
- the code you changed,
- `REPORT.md`: (1) the design of each part, (2) what `architecture.pdf` shows and how it differs from the kit's default,
  (3) Round 3 results, quoting from your logs the settlements, the Players rank and the refusal reasons at the time you
  submit, next to the baseline numbers you wrote down in the week 10 LAB,
- `architecture.tex` and the `architecture.pdf` built from it.

No squash. No API key or private key in any commit.

## Submit by PR

```bash
git fetch upstream && git merge upstream/main
git switch -c week-08
git add submissions/<student-id>/week-08
git commit -m "week-08 <student-id>"
git push origin week-08
# open a PR to Q00/ai-agent-engineering-101, title: [week-08] <student-id>
```

The PR must be opened before Monday, November 23, 23:59. If you already opened a PR, you have submitted; until the deadline you may push more commits to the same PR. Change nothing outside `submissions/<student-id>/`.

## Next

The market is paused from 23:59. Round 4 opens on https://market.ourolabs.ai on Tuesday, November 24, at 19:00 KST. Week 12 covers what you need to start, and the final exam.
