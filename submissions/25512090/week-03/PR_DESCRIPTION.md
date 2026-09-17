## What I built
- Implemented a KBO Draft scenario based on the Contract Net Protocol with 1 Manager (KBO Draft Office) and 3 Contractors (Hanwha Eagles, Lotte Giants, Kiwoom Heroes).
- Established economic constraints where each club starts with a budget of 100, and player contract costs range from 15 to 20, allowing strategic budget management and disqualification mechanics.
- Configured 3 experimental conditions: `baseline` (distinct team scout colors), `homogeneous` (generalist scouts), and `overconfident` (extreme confidence bidders).

## What I tried and discarded
- Initially included team names directly in task descriptions, but discarded it because it preempted the LLM's autonomous evaluation of player attributes.
- Tested higher contract costs (35-40), but found that teams exhausted budgets too quickly or failed to show competitive multi-player acquisitions; lowering costs to 15-20 created an engaging environment where aggressive teams could acquire multiple prospects.
- Evaluated homogeneous and overconfident modes across 9 clean runs, observing how lack of specialization or blind overconfidence skews allocations away from gold contractors.

## How to run
```bash
python submissions/25512090/week-03/manager.py
python scripts/check_week03.py submissions/25512090/week-03
```

## Checklist
- [x] python scripts/check_week03.py submissions/25512090/week-03 passes locally
- [x] tasks.json committed before any run
- [x] Run logs committed under logs/, one per run
- [x] No API keys anywhere in the diff
- [x] History is not squashed
