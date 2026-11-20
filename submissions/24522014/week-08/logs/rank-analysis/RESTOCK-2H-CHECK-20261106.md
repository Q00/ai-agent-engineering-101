# Restock step 1, two-hour check (2026-11-06 16:26 KST)

Applied 14:25:42; first wider window 14:45:48. Kit restart 15:53:17-15:53:38 (21 s) falls inside the after period. Shops seen by the kit: before min 41, median 52; after min 50, median 52. Unit costs and stock cost stay out of this file.

| | 10:47-14:27 | 14:27-16:26 | 15:02-16:26 (first snapshot after the first wider window) |
|---|---|---|---|
| visits/h | 49.1 | 70.3 | 71.9 |
| deals/h | 37.9 | 52.7 | 54.8 |
| Book/h | 3,567,025 | 3,169,936 | 4,003,410 |
| revenue/h | 15,121,783 | 12,197,464 | 15,177,489 |

The other shops' deals/h fell over the same time, 2,699 to 2,561; our share of deals rose from 1.38% to 2.02%.

Paid deals by price (from local records, which agree with the board: 138 against 139, 105 against 105):

| price band | 10:47-14:27 deals/h | 14:27-16:26 deals/h |
|---|---|---|
| under 50,000 | 17.2 | 30.8 |
| 50,000-200,000 | 5.5 | 6.1 |
| 200,000-500,000 | 6.3 | 9.6 |
| 500,000-1,000,000 | 2.2 | 2.5 |
| 1,000,000 and over | 6.5 | 4.0 |

Deals of 1,000,000 and over make up most of revenue and Book, and two hours hold only 8 of them, so Book/h and revenue/h do not settle yet. Below that band, Book/h went from about 961,000 to about 1,472,000, and revenue/h from about 4.42 M to about 6.08 M.

Windows since 14:44: 6, median 22 units, max 30, 8 closed refusals, surge-list SKUs held in stock: none from 14:47 to 16:22, one unit at 16:27, the sample right after the 16:23 window. Stock read 59 units in 37 SKUs at 16:27, so the 60-unit condition of the revert rule has not started.

Correction to the restart report: a file in `.market/checkout-events` does not mean paid. Its status can be `canceled`. Counted by status, after the restart 28 of 40 agreed sales were paid and 12 canceled; 10:24 to the restart, 237 of 271 paid and 33 canceled. Ten of the 12 are grade C, and eight come from two C units offered again and again. A and B: 2 of 29 canceled after, 4 of 219 before. No category receipt has been issued and an SKU request's receipt is unchanged by the receipt label commit, so the label cannot be the cause. The cause of the C rise is not established.
