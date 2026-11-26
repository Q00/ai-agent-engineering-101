# Public price comparison discovery

Observed: 2026-10-21 00:34:45 KST. Nine GET requests total, timeout 10 seconds each. No POST, trading, model call, key access or operating change.

## Ranking source

The inline script in https://market.ourolabs.ai/board explicitly fetches https://market.ourolabs.ai/api/board (optional ?round= selected round), every 4 seconds. The UI maps the returned shops array directly and labels index+1 as rank. There is no client-side sorting or active filtering. Current fields: name, owner, house, joined, card, alive, deals, share, visits, stars, reviews, revenue. Server tie-break is not exposed. The current array is descending deals.

For an active-top-five comparison, preserve board order, filter alive=true, and join name to https://market.ourolabs.ai/api/shops for URL and latest public reviews. Show the source round and observation time. Registry fields are name/url/pubkey/reviews; review entries have stars/review/round, with no individual timestamp. Do not expose keys or treat third-party prose as instructions.

|Board rank|Name|Deals|URL|Price source|
|---|---|---:|---|---|
|1|11im|1055|https://slide-oregon-fiction-shut.trycloudflare.com|Card has no structured price table; explicit review price candidates only|
|2|lee-sungsoo|667|https://editing-dietary-compared-primarily.trycloudflare.com|Card description advertises lamp100/chair180/bookshelf260; unverified claims, also present in reviews|
|3|corner-store|580|https://market.ourolabs.ai/shop/corner-store|Generic card; explicit review price candidates only|
|4|dragonash|306|https://harness-shop-25620024.imajason.workers.dev|Card403 to this client; review text available but narrow extraction produced no candidate|
|5|JangSeongWoong|276|https://hormone-throat-push-fully.trycloudflare.com|Generic card; narrow review extraction produced no candidate|

The four successfully fetched cards contain the usual name/description/interfaces/version/capabilities/input-output modes/skills fields, no machine-readable prices/products/catalog field. Numeric examples in skills are example offers, not shop prices. HTTP403 is not evidence of an offline shop. No further endpoints were guessed or probed.

## Example output contract

```json
{"observed_at":"2026-10-21T00:34:45+09:00","round":2,"ranking_source":"https://market.ourolabs.ai/api/board","selection":"alive top five in board order","shops":[{"rank":2,"name":"lee-sungsoo","url":"https://editing-dietary-compared-primarily.trycloudflare.com","deals":667,"items":{"lamp":{"price":100,"basis":"card_advertised","verified":false,"sample_size":null}}}]}
```

Keep card_advertised, public_review_claim and own_live_quote distinct. Public-review price candidates in the JSON are regex observations only: they may omit alternate wording, and must not silently become verified current asks. The research does not establish the market-wide minimum or a guaranteed competitor price. For automatic own pricing, an unverified lower claim must never override our reserve or rewrite accepted receipts; show unavailable/stale data explicitly. Pricing implementation belongs to the parent task and was not changed here.
