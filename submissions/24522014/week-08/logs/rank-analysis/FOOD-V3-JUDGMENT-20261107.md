# Food trial v3, 24-hour judgment (2026-11-07 14:47 KST)

Read with `analyze_food_trial.report(root, now, "v3")`. No setting changed. Unit costs stay out of this file.

No stop signal: `stop_signals` is empty.

| stop rule | limit | result |
|---|---|---|
| discards | 1 or more stops | 0 |
| sold within 24 h | under .5 stops | 4 of 4 |
| profit per deal against goods median | under .25 stops | .966 |

| category | grade | price | hours to sale |
|---|---|---|---|
| 즉석조리식품 | C | 13,854 | 0.26 |
| 찌개/탕류/장류 | B | 25,800 | 0.09 |
| 과자 | A | 8,801 | 0.04 |
| 주스/코코아/생수 | B | 19,953 | 0.03 |

The trial used its whole limit of 4 units on 11-06 about 14:46 and none has been bought since, so food stock is 0.

Limits of this result:

- Four units is a very small sample, and all sold within 16 minutes.
- The goods median comes from all signed offers with a cost, not only completed sales.
- Reviews of these four units were not found in `logs/settlement-reviews` or local `.market` review files, so stars for them are unknown.

## Forum after topics reopened

The 09:01 check expected topics back from 10:44. That is now observed.

| period | posts/h | SKU note n, median | n=5 notes | category notes |
|---|---|---|---|---|
| 09:01-10:44 | 5.2 | 5 | 6 | 0 |
| 10:44-13:00 | 9.7 | 13 | 3 | 5 |
| 13:00-14:47 | 11.2 | 39 | 0 | 9 |
