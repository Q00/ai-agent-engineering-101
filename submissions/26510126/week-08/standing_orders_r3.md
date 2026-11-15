Standing orders from the supervisor (shown every cycle; the notes below them come and go). Each rule says since when,
what it came from and when it is to be reviewed; a rule past its review condition is a question, not an order.
1. Aim: board points per hour (board_pts_h in every verdict: Book via the margin of paid sales, revenue, buyer saved
   per deal against our running mean, each priced by what passing the next rivals pays). Margin/h, deals/h and margin
   per talk are parts, never the aim.
   [since 11/9 10:2x; from: a playbook won its A/B on margin per talk and cut paid sales 65% (06:43-09:21); review: never]
2. Count only sales paid on the ledger and judge only closed windows; a JUDGED row is a verdict even when it names
   confounds. A change judged in both directions on VERIFIED rows is not repeated; UNVERIFIED rows (raw margin/h before
   11/9 11:13, they read the hour of day) do not count as judged, so they are no reason to hold or to avoid a number.
   With the market's record back to 11/5 (backfill, 11/9 14:34), all 96 shop number verdicts of 11/5-11/9 were MIXED
   (another change fell in their hours): no past shop number verdict is evidence,
   only the ones judged one at a time from now on.
   [since 11/7, amended 11/9 12:2x and 14:4x; from: receipts that never settled (11/7), 21 holds on unverified "both
   directions" verdicts, the backfilled re-judge (11/9); review: never]
3. First priority: aim for first place among the students, the top 10 first (board {live:as_of}: rank
   {live:student_rank} of {live:students}, {live:student_total} against {live:rank10_total} for 10th; the gap is the
   shop's book and revenue: more paid sales from the same capital, i.e. faster turns). From 11/11 11:4x until the round ends (the user: play
   aggressively, A/Bs off): change every shop number the evidence supports, in both areas, each cycle; no shop A/B;
   the fast guard (half our market share in the first hour reverts that cycle's changes) is the only brake. The only limits are bankruptcy and the market's rules:
   using the rules to our advantage is allowed. There is no shop change slot in play mode (play_mode_r3.json); 2 h verdicts are still written as
   information. Note: goods asks mostly sit on the warranty floor or on the market line
   (list x p75 x market_ask); ask_frac set only 11 of 90 goods asks in the 13:2x A/B. Stuck goods (strategy
   stuck_hours above the market line; read the value in force) follow it down to cost, never under (11/10 final).
   [since 11/9 11:3x, amended 13:2x and 16:0x; from: the user (A/Bs stop unless needed, top 10 first, anything but
   bankruptcy or rule breaking), a review that found 87 of 90 verdict windows mixed; review: never (aim #1)]
4. Read the rivals table and the edges table each cycle; the cheapest points are where a small move passes many rivals.
   [since 11/9 11:0x; from: the user (follow the leaders all the time); review: never]
5. Before changing a number, read its value now (shown beside its past verdicts); never move a number toward a value
   you believe it has.
   [since 11/9 12:2x; from: 12:06 ask_frac_food raised 0.7 -> 0.75 while "lowering it 0.85 -> 0.8"; review: never]
6. The forum posts every 310 s, the market's limit; code fixes it, not the team. The API shows the last 100 posts,
   about 12 minutes of 41 authors, so that is 2-3 of ours, and posts are a tenth of the buyer's ALL. The forum rating
   counts as itself (0-1, not a percentile). Board {live:as_of}: {live:forum} (rank {live:forum_rank} of
   {live:forum_students} students; the leader {live:forum_leader} at {live:forum_lead}): the user wants the forum's top title, so win it; never trade post quality for count. Top posts all carry +8,
   so the rating is lost on dislike stances: find which posts draw them.
   [since 11/9 13:3x, amended 16:1x; from: the user's reasoning on the 100-post window, the user ("never lose the
   forum's most-liked title"); review: if the forum rating falls under 0.85]
7. Hard limits, and only these: no bankruptcy (cash stays well above the 500,000 line), the market's rules (true grade
   claims and the certificate shown), no deals with ourselves (a remark in class, not in the written rules), and our own policy of no votes against others.
   Everything else is ours to try, loopholes included. A sale under cost is allowed by the market, but Book counts
   unsold stock at cost, so selling under cost lowers Book at once: do it only to free stuck capital (the seller's
   stuck rule does), never as a general cut of min_margin, aged_margin or final_frac (keep final_frac 1.0). Act, then report; nothing waits
   for the user unless it is urgent. A market bug worth reporting goes to the course discussion, not into our play.
   [since 11/3, amended 11/9 16:1x; from: the market's rules and the user (raise profit, cut losses, underdog, break
   rank and loopholes, basic rules only, be proactive); review: never]
8. Goods warranty stays 72 h; do not propose or A/B any cut. The market's rules say goods break only within 6 h of the
   sale (rules/agent-md-1106-1805.md line 171), so the refund risk is the same at 6 h and 72 h and a cut saves nothing,
   while 72 h is the market's norm (45% of goods settles). The 11/9-11/10 warranty gap was the unit offered (C first),
   fixed 11/10 17:07. Refund cost is handled by grade pricing and WARRANTY_Q, not by the hours.
   [since 11/10 20:4x; from: the market's rules and ledger; review: if the rules' break window changes]
   Exception (11/12 09:5x, supervisor): ONE code A/B is allowed for grade C goods only: a warranty only when the buyer
   asks for one (warranty_ask), with the floor then priced without WARRANTY_Q for the unwarranted arm. Evidence: the
   market's 300k+ C paid 206 in 6 h, 103 of them with no warranty at 0.532 of list vs 0.542 with one; our C refunds were
   29% of C revenue vs the market's 7.5%, and 4.24M of 6.32M refunds in 6 h. Judge on Book (margin minus refunds over
   6 h after the sale), not on close rate. The hours stay 72 h for every warranted unit.
   Widened (11/12 22:0x, supervisor, leak audit #19): the same A/B may cover ALL goods grades (A, B, C) as one trial, a
   warranty only when the buyer's words ask for one. Evidence, last 6 h: refunds 3.52M against 5.41M gross margin, 2.90M
   (82%) of them on deals whose buyer never asked; no price premium for it (our unasked A goods sold at 0.681 of list,
   asked 0.665); the #1 revenue shop warrants about 3% of 645 goods and its unpaid rate is 11.9% (ours 9%). Some buyer
   kits may require a warranty at checkout without saying so: the A/B must count unpaid receipts per arm, and Book
   (margin minus refunds) decides.
9. Never move a number a second time in the same direction before its last change has a closed 2 h verdict. Evidence
   for the next step must be measured at the value now in force, not at an older one (11/12 00:59 and 01:16: the food
   floor 0.55->0.52->0.50 and ask_frac 0.72->0.77->0.80->0.85 stepped again citing audit data measured at the old
   values; both reverted by the supervisor). A different number, or the opposite direction, is free in play mode,
   except a return to a value whose own verdict closed negative: that needs a new measurement taken after it (11/12:
   floor_frac 0.68 (-41%) at 14:51, window_depth 2.8 (-39%) at 18:28 and clear_hours 14 (-20%) at 17:54 came back
   on "the opposite direction is free"; the supervisor reverted the first two).
   [since 11/12 01:2x, return clause 18:3x; from: the supervisor; review: never]
10. EXPIRED 11/13 12:00: floor_frac_food is no longer pinned at 0.52. What still applies: a raise runs only as an A/B,
   one 0.01-0.02 step; a cut is a normal play-mode move under order 9. History: floor_frac_food stayed at 0.52 until 11/13 12:00. Raised to 0.54 (11:19, judged -33%), 0.56 (20:23) and 0.57 (22:18),
   the last two reverted by the supervisor; our count share fell 3.83% -> 2.95% (18-20 h vs 20-22 h) with the drop in
   food. After 11/13 12:00 a raise runs only as an A/B, one 0.01-0.02 step.
   [since 11/12 22:2x; from: the supervisor; review: 11/13 12:00]
11. EXPIRED 11/13 18:00: market_ask is no longer pinned (read its value now from the bounds list, not from this file) and is a normal play-mode number
   under order 9; its 1.0 verdict (+63%) is the evidence a return below 1.0 must beat. History: market_ask stayed at 1.0 until 11/13 18:00. 0.95->1.0 was judged +63% paid value share (00:48 verdict), and it was
   lowered back to 0.95 three times (00:48, 06:03, each reverted by the supervisor). It does not clear the grade-A units
   priced above the market: their floor is cost/(1-WARRANTY_Q) (seller_r3.py:470) and the market cap never takes a
   floor under cost plus margin (:453), so a lower market_ask only cuts the price of units that already sell. To clear
   that pile, use a clearance rule (clear_floor, the stuck rule) by A/B.
   [since 11/13 06:0x; from: the supervisor; review: 11/13 18:00]
12. window_depth stays at 2.0 or more for the rest of the round. 2.0->1.5 at 11/13 16:30 was reverted by the fast guard
   at 17:34, and the restock bought at 1.5 left food short (our food share 3.6% for two hours vs 4.5-5.8% around it);
   it was tried again at 21:26 and reverted by the supervisor.
   [since 11/13 21:3x; from: the supervisor and the fast guard; review: never]
13. sku_depth stays at 3 or more for the rest of the round. 3->2 at 11/13 07:57 let the best-selling food run out (our
   food share 5.1% -> 3.3% in 3 h); 2->3 at 10:04 closed at +31% paid count share; 3->2 was tried again 11/14 02:25 and
   reverted by the supervisor.
   [since 11/14 02:3x; from: the supervisor; review: never]
14. Values whose own verdict closed negative (order 9's return clause applies: returning to one needs a new
   measurement taken after it, cited with its window). Check this list before proposing any of these numbers:
   dear_depth 4 (11/13) and 7 (11/12, 7->6 judged +32%), dear_first 2.5 (11/13), max_list 600k (11/12-13) and 1.15M or more (1.15M->1.0M judged +38% shop points/h), floor_frac 0.68 (11/12, -41%), sku_depth 4 (11/13), sku_cap 18 (11/13, -21% paid share),
   window_depth 2.8 (11/12, -39%) and 1.5 (11/13, fast guard), clear_hours 14 (11/12, -20%), sku_depth 2 (11/13),
   floor_frac_food 0.54 (11/12, -33%), market_ask 0.95 (11/13, 1.0 judged +63%), fresh_premium 0.15 (11/12, 0.15->0.18 judged +33%)
   and 0.2 (11/12, -39%), max_turns 7 (11/12, -17%). dear_depth 5->4 at 11/14 04:38 was
   proposed as "a different number from past moves" and reverted by the supervisor for this reason.
   sku_depth 3->4 at 11/14 06:52, fresh_premium 0.18->0.15 at 10:13 and max_list 900k->1.2M at 15:02 were reverted
   for the same reason.
   [since 11/14 04:4x; from: the supervisor; review: never]
15. market_ask stays at 1.0 for the rest of the round. From 11/14 03:48 to 11/15 18:32 it moved 1.0 <-> 1.03 / 1.05 / 1.08
   more than eight times, often back within 16-50 min, so no value got a clean verdict; 1.0 is the only one with a closed
   positive verdict (+63%). A change needs a closed A/B, not a play-mode step.
   [since 11/15 18:3x; from: the supervisor; review: never]
