# Public storefront observation

- Observed with Codex in-app browser tab 7, https://ax-week07.treasuresky.xyz/, after the 1.3.0 deployment on 2026-10-21, approximately 10:01 KST. Browser work stayed in the background; the user's OpenAI usage tab was not changed.
- Both accessibility tree and rendered screenshot showed the price comparison above the basic catalog and the three highlighted opening ceilings: lamp <=100, chair <=180, bookshelf <=260.
- Each highlight said 공동최저 / tied with lowest observed first ask, explicitly covering five other active shops. The corresponding peer row for lee-sungsoo showed 100/180/260; other peer rows and observation times were visible.
- Snapshot label was 09:56:05 KST, expiry 10:11:05 KST. Text stated 10-minute server refresh, 60-second page polling, and 15-minute expiry. Expired/failure behavior was exercised separately in the offline JS tests, not by changing the live browser's clock or feed.
- Coupon terms stated there is no further reduction at the minimum price and no review/rating requirement. No global-cheapest or identical-quality guarantee was displayed.
- This verifies the deployed presentation. It does not establish advertising exposure to market buyers, higher visits, higher conversion, or a ranking effect. The default shops.json selection input does not include this storefront HTML.
