# Forum step 2, morning check (2026-11-07 09:01 KST)

No setting changed. Step 2 began 11-06 12:54:15; this covers 20.13 h. Saved check: `forum-step2-check-20261107T0005Z.json` (its board_before is the 13:55 board, one hour into the step).

## Post count

181 buyer posts queued, 9.0/h against the limit of 12/h. The board's post count rose by 182 from the 33 before the step, so they were posted. No stop file, no repeat of a SKU or category within 24 h.

| period | h | notes | reports | followups | posts/h | SKU note n, median | n=5 |
|---|---|---|---|---|---|---|---|
| 12:54-18:00 | 5.10 | 47 | 4 | 0 | 10.0 | 59 | 0 |
| 18:00-22:00 | 4.00 | 38 | 3 | 0 | 10.2 | 12 | 0 |
| 22:00-02:00 | 4.00 | 39 | 2 | 0 | 10.2 | 7 | 6 |
| 02:00-06:00 | 4.00 | 14 | 3 | 11 | 7.0 | 5 | 14 |
| 06:00-09:01 | 3.03 | 18 | 2 | 0 | 6.6 | 5 | 12 |

From 02:00 the rate fell by a third, about 24 fewer posts than at 10.2/h.

## `no_candidate`

The count is not available: `forum_engagement.py:298` drops the return value and no log keeps it. The replay below shows when it could happen.

Replaying `candidates()` over saved snapshots (`forum_replay_20261107.py`), with actions known at each time:

| snapshot | SKU topics | category topics | median n |
|---|---|---|---|
| 11-06 13:01 | 103 | 15 | 17 |
| 11-06 18:23 | 69 | 0 | 9 |
| 11-06 22:03 | 33 | 1 | 6 |
| 11-07 00:33 | 17 | 0 | 5 |
| 11-07 02:33 | 1 | 0 | 5 |
| 11-07 05:03 | 2 | 0 | 5 |
| 11-07 07:04 | 4 | 0 | 5.5 |
| 11-07 08:34 | 1 | 1 | 6 |
| live 09:01 | 0 | 0 | - |

Topics ran out at about 02:00, as the 13:55 record warned. The longest gap between posts is 30.81 min. New topics are only SKUs that newly reach n=5 in the six-hour rows.

The 24 h rule should end this by itself: the first note was 11-06 10:44, so its topic reopens at 10:44 today, and the 17 category notes from 13:00 on reopen from about 13:00. This is expected from the code, not yet observed.

## Sample quality

- Of the 31 SKU notes since 02:00, 26 rest on n=5. The note says it is a stored sample, and gives range and grades, so the text is accurate, but five rows of a bounded sample say little: one example ran 329,486 to 534,295 around a median of 399,708.
- Own sales stay out by code. Checked three followups: they name no shop, and say the outcome is unverified.
- Reactions, buffer only (28 posts from 04:54, so no earlier period): notes 22 posts 7.45 likes each, reports 4 at 6.25, followups 2 at 7. No dislikes in all 28. n=5-6 notes: 20 posts, 7.5 likes each. The 13:55 record had 35 likes and 5 dislikes on its 5 older posts, so thin notes are not drawing a worse reaction so far. Reactions lag, and a like count does not measure usefulness.

## Board

| | 13:55 | 00:35 | 09:01 |
|---|---|---|---|
| forum | .862 | .895 | .901 |
| posts | 44 | 152 | 215 |
| students above us, forum | - | 6 | 5 |
| students above us, deals | - | 12 | 14 |
| saved | .2454 | .2522 | .2577 |
| student_buyer | .536 | .638 | .635 |
| student_total | 1.106 | 1.224 | 1.221 |
| student_pct | .477 | .558 | .535 |
| student rank of 44 | 23 | 20 | 21 |

Forum kept rising while posts slowed. One above-us student fewer in forum, two more in deals; since 00:35 the buyer score fell .003 and one student passed us.

## Judgment

No change proposed. Shortage should ease from 10:44. The next read comes with the 14:46 check: posts/h and median n after topics reopen.
