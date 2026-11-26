# Current-round completed-trade benchmarks in seller1.4.1 (Codex)

The user supplied a PRICE BY ITEM screenshot and requested using dashboard statistics in selling,after finishing the provider/week setup. The original screenshot is preserved as user-price-table.png. The source was identified from the public /dashboard page as GET https://market.ourolabs.ai/api/stats . Its active-round aggregate is rounds[str(round)].items; /api/board does not itself contain these price rows.

## Pricing and truthful presentation

The existing ten-minute price collector now also reads numeric current-round trade statistics. Only lamp,chair,bookshelf enter the cache/model. A mismatched round,HTTP/schema failure,non-finite/out-of-range statistics,stale snapshot or fewer than100 completed trades cannot activate the mean-based policy. The three-peer minimum,per-quote freshness,reserve protection and original ten-percent baseline cap remain.

With sufficient fresh evidence,the new maximum opening ask is max(reserve,min(baseline_cap,lowest_observed_opening_ask+max(1,floor(reserve*0.02)),floor(completed_trade_mean))). This relaxes the former second-lowest-minus-one constraint only within the small existing margin and the observed trade mean. It is an opening ceiling; the model still chooses the actual ask. Actual feasible offers still settle immediately at the exact offered amount.

The public quote states market round,completed-trade mean,N,and actual ask separately from peer opening-ask comparisons. Metadata carries the numeric comparison and observation time. The final number in quote text remains the current ask. The page machine terms and card now identify strategy1.4.1. No global-lowest claim,review incentive or cross-shop message was added.

The collector's former60-refresh lifetime was approximately10 hours. It now has1008 ten-minute slots within the independently enforced week deadline. Fresh-cache restart waiting remains intact,so deployment used one bounded ordinary refresh to prime the cache before cutover. That refresh made no purchases or reviews and mounted no model credentials.

## Verification and observed release

- New tests11/11 cover exact102/183/265 ceilings,cache round-trip,small/stale/missing-round/invalid evidence,public quote text and collector stats failures. Full offline suite265/265 passed; scoped lint/types passed. Two initial full-suite failures were preserved: a proposed startup refresh broke fresh-cache waiting and was reverted; the old sixty-cycle expectation was updated to the user's one-week1008-cycle contract. No test was skipped.
- Actual primed R2 data: means102.8/183.9/266.8,N9785/9765/9926. Five peer observations per item yielded ceilings102/183/265; all three are above the observed peer minima100/180/260 and are marked higher. [Primed source cache](primed-cache.json),[isolated image preflight](preflight.log).
- Source443e454,image1.4.1-week-openai-daily5000,IDsha256:c97735a607cd0d153b0a6781f8820dd59054bd6e1e926c7ae052f37de1f1e03a,deployed2026-10-21 17:47:29KST. Same URL,registered signing key,persistent seller DB,quota ledger,baseline4354 and baseline hash. [Build](build.log),[deployment](deploy.log).
- Public real model negotiation opened lamp102 with text stating R2 completed-trade mean102.8,N9785. The first100 offer completed at100 and the receipt verified with the registered public key. No result or review was submitted to the market. [Trace](public-trade.log),[receipt](public-trade.json).
- Final read: running/healthy,deadline2026-10-28 17:15KST,limit_mode=openai_daily,paid_daily_limit5000,cumulative observed5310 and unchanged baseline. The paid ledger remains empty/0600. OpenRouter reports75 free requests used/1000,925 remaining,credits20,total_usage0. Hermes ID/start/restarts are unchanged. [Runtime and usage](after-runtime.log).

The policy and live quote work; increased revenue,visits,rank or review quality has not been established. Mean prices are a mixed market aggregate,not a randomized comparison or a guaranteed selling price. The instructor structural checker passed on the current sources while reusing the earlier25/25 test report; [output](structure.log). The official live25-case course test was not rerun for1.4.1. Auto buyer,rejoin and the stopped15-minute monitoring heartbeat remainOFF/PAUSED.

Rollback: use preserved1.4.0-week-openai-daily5000 and retain all persistent mounts and quota records. This restores the prior pricing while preserving the authorized weekly/provider budget. Do not restore the older OpenAI-only or aggregate-only configurations silently.
