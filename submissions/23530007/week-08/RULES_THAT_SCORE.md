# Rules that score (the short block both strategists and the curator read; 11-11)

Source of truth: `.market/agent.md`. Round 3 ends 2026-11-17 23:59 KST. Round 4 opens right after (Maroo testnet, real transfers, 1% fee each side).

## Buyer
- Buyer ALL = 0.5 x saved percentile + 0.3 x forum rating (as is, 0-1) + 0.1 x deals percentile + 0.1 x posts percentile. The class rank uses percentiles among STUDENTS only (46 student buyers): one saved rank = 0.0111 class buyer, one deals or posts rank = 0.0022.
- saved (patch 3.6, live 11-12 ~08:40 UTC, all past deals rescored: ours .3081 -> .7163) = sum over deals of (list - price) / (list - unit cost), the share of the shop's margin we kept, capped at 1; a deal that served nothing counts -1; **a broken unit under warranty is refunded and counts 0**; divided by (deals + 100). Cost by grade (share of list): A .55-.75, B .40-.60, C .25-.45, so the same price/list scores higher on a grade A unit (thin margin) than on C.
- Our code still prices on the old scale 1 - price/list (buyer_score.json m_now/m_star are on that scale; m_now_board is the board's new value). Rough mapping: new v ~= old v / (1 - cost/list); our 11-12 deals: old .44 ~ new .85, board mean .72. A deal helps only if its new v beats the mean the score will END at.
- Market break rates by grade (fully observed, 11-11): A 3%, B 8%, C 29%. A warranted unit is worth (1 - p_grade)(1 - x).
- The market assigns our order mix (about 63% beds/sofas/bedding, 37% food); we cannot choose more food, only which shop and at what price.

## Shop
- Shop ALL = 0.5 x book percentile + 0.3 x revenue percentile + 0.2 x stars_rank percentile (students only for the class score: book 1st is worth ~0.011 class shop, one revenue rank 0.0067, one stars rank 0.0044). stars_rank = one mean per reviewing account over paid deals only.
- Warranty refunds leave the book (and revenue); storage costs 1%/day of goods cost; food past its date leaves the book at cost.

## Conduct (user)
- The user sets goals; the agents own the operating settings and alert on changes. No unjustified hits on classmates: dislikes only on posts that speak badly of us; never buy dislike power; no per-account worse terms (better terms for everyone is fine). Never wait on the user for anything reversible.

## Goals (user, 11-11)
- Finish 1st overall. Shop: keep the current trend (book 1st above all; if book and stars conflict, book wins) and raise stars without price cuts. Buyer: saved first; accept fewer goods deals for it. Buyer TARGET (user, 11-11): reach class buyer 13th (our best so far, .769 on 11-10) and HOLD it: per-deal v >= .42 (food .42-.45, goods only >= .33 realized), breaks under 6%, no gambles for 10th; do not trade the deals percentile away beyond what the saved gain pays back.
