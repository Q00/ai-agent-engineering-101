# Correction to the restart check of 2026-11-06 (new record, the first report is left as sent)

The restart check sent at about 16:07 KST said that all 21 agreed sales after the restart were paid and that all 39 in the 30 minutes before it were paid. Both are wrong. The check counted a file in `.market/checkout-events` as a payment, and that file's `status` can also be `canceled`.

| Period | Agreed | Completed | Canceled | Other |
|---|---|---|---|---|
| 30 minutes before the restart | 39 | 34 | 5 | 0 |
| 10:24 to the restart | 271 | 237 | 33 | 1 with no event |
| Restart to about 16:28 | 40 | 28 | 12 | 0 |

Still true: no agreed sale was lost to the restart, since all 39 offers of the 30 minutes before it ended as completed or canceled. The one offer without an event was made at 14:48:10, about an hour before the restart.

From now on payments are counted by `status == "completed"` and compared with the board. By that count the local records agree with the board: 138 against 139 for 10:47-14:27, 105 against 105 for 14:27-16:26.

Also wrong in the first report's reading: "결제 거절은 없습니다" (no payment refusals). Refusals were 12 of 40. They are mostly grade C, and `logs/rank-analysis/c_grade_check_20261106.py` holds the follow-up.
