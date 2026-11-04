# 2026-11-04 02:2x — restock: breadth or depth?

The user's goal: win back the early loss by selling many units at a profit (strategy v19, first priority).

Question: should the buy list go wide (more of the market's best-selling SKUs, a few units each) instead of deep
(a few SKUs we missed, up to 8 units each)? Decide it now with the playbook numbers.

What we saw:
- House buyers post "침구 구합니다, 예산 91만 원대, 파는 가게가 없네요" (#1345, #1330, #1356). Each order accepts only
  certain SKUs of its category, and the kit shows a buyer only the shops that hold one of those SKUs. A shop without
  them is never visited, so these orders never show in me.missed. Our forum reply (#1349, Ge62449c9) does not help
  when that SKU is not in the order's list.
- The market's best sellers are the SKUs orders accept: 침구 sold 1,600+ in 6 h at a median 75% of list; G0be9ed07
  (소파패드) 772 sold, G07ec17d5 (매트리스) 675.
- The buy list now: our missed SKUs first (apron G53e73f8c 39 asks in 2 h, Ge62449c9 18), market top 5 x 2 units.
  At 02:17 the cash we may spend is ~2.33M but the stock cap leaves ~0.93M, and 4 mattresses (G0f415407, worst cost
  ~199k each) take most of it, so no market best seller gets bought this window.

Playbook numbers you can set (playbook.restock): cover_windows (0.5-6), market_top (0-30), market_units (0-8),
max_line_share (0.05-1: the most one line may take of the window's money; 1 = no cap). Stock cap: limits.max_stock_share
(at most 0.5 by yourself; the user set 0.6).

# 2026-11-04 17:1x — the cash is large: should we spend more of it, and how?

The user asks: the balance is large (31.4M, board profit +23.4M); is spending it every window the right strategy,
and how would you (the strategy agent) judge it?

What we measured at 17:1x:
- Free cash 17.93M (balance 31.45M - floor 0.5M - warranty reserve 13.01M = 10% of 130.1M sold in 6 h).
- The next window's plan spends 17.93M, all of it, on 7 lines; 40 buy-list lines still lack stock and 33 of them are
  left out for cash (Ge62449c9 10, G6b9db161 10, G5e3784e6 8, G5cd4ffbc 7, ...).
- Refunds: 43 of 847 deals broke (about 5%, our goods are high-list), so the 10% reserve is about twice the expected
  refunds of a 6 h window.
- The buy list counts each unit at the worst-case cost (75% of list); real costs run near 62% of list.

Options you can judge: (1) count units at the expected cost instead of the worst case, (2) a second plan in the same
supply window with the cash left after the first, (3) a lower warranty reserve share, (4) leave it as it is.
Recommend with numbers (cash, stock turnover, refunds, profit percentile).
