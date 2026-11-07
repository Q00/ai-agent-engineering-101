# Correction: student_buyer explanations of 00:35 and 09:01 (2026-11-07 19:10 KST)

This corrects two earlier records without changing them:

- `MORNING-PROBE-20261107.md` section 4, "student_buyer .648 to .638"
- `FORUM-CHECK-20261107.md`, the board section

## What was wrong

1. **Old formula.** Patch 3.5 (lecture notes `affeb318`, 11-06 15:53 KST) counts the forum rating as itself in buyer ALL; the other parts stay percentiles. The official score switched between our snapshots of 17:53:31 and 18:23:33 KST. Both records still scored forum as a percentile.
2. **Wrong group.** The records matched student buyers by player name and found 24. Matching by each player's buyer row gives 43 student buyers. With 43 and the new formula, the reconstruction meets the board's `student_buyer` within .0005 on all four boards below.

The 00:35 record's "forum percentile .7391 to .6957, a student reaching a tie with us" is therefore wrong, and so is its stated level gap (.674 against .648).

## Recomputed, 43 student buyers, patch 3.5

| board | student_buyer (board / ours) | saved: percentile, points | forum: value = points/.3 | deals: percentile, points | posts: percentile, points |
|---|---|---|---|---|---|
| 11-06 20:33 | .648 / .6478 | .6429, .3215 | .889, .2667 | .2857, .0286 | .3095, .0310 |
| 11-07 00:35 | .638 / .6376 | .6190, .3095 | .895, .2685 | .2857, .0286 | .3095, .0310 |
| 11-07 09:01 | .635 / .6346 | .6190, .3095 | .901, .2703 | .2381, .0238 | .3095, .0310 |
| 11-07 18:34 | .635 / .6345 | .6190, .3095 | .905, .2715 | .2143, .0214 | .3214, .0321 |

## Corrected explanations

- **20:33 to 00:35, -.010:** saved fell one place among students (.6429 to .6190, -.0120); our saved still rose, .249 to .2522. Forum added +.0018 by its own rise. It was not the forum percentile.
- **00:35 to 09:01, -.003:** deals fell .2857 to .2381 (-.0048) while ours rose 1410 to 1556; others grew faster. Forum +.0018. The 09:01 record pointed at deals and that part stands; its forum count of students above us does not bear on the score.
- **09:01 to 18:34, -.0001:** forum +.0012 and posts +.0011, against deals -.0024.

## What follows for the buyer side

- **Forum:** points move with our own rating only, .003 per +.01, whatever others do. A paid dislike against us (also patch 3.5: `value -N`) would lower it directly.
- **Posts and deals:** still percentiles. Each place among 43 students is worth .1/42 = .0024.
- **Saved:** still the largest part, .5 x percentile. One place is .0119.
