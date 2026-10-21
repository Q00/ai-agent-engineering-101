# Seller1.4.0: bounded opening margin and first-feasible settlement

(Codex) The user stopped15-minute trend detection and requested an improvement based on the collected observations. Monitorax7 was changed toPAUSED through the app tool; ax7-1 remainsPAUSED. Seller operation and its existing600-second comparison collector retain the21:00 KST/10000 observed-call contract. No autonomous buyer or rejoin was started.

## Evidence and change

The monitored shop had low traffic despite100/180/260 tied-lowest openings. Higher-priced peers still completed trades; this does not prove raising asks improves revenue or attracts visits. Own retained log analysis counted355 bargaining turns:250 below basic reserve,83 feasible,and22 unparsed. Long bookshelf examples climbed250→254→256→257→258→259→260. They do not demonstrate repeated refusal of a valid260 offer. [Derived log analysis](log-analysis.json).

1. With at least3 fresh distinct shop-name observations, new basic opening ceilings use the second-lowest observed ask minus1, bounded by the original10% opening cap and lowest observed ask plus at most2% of the basic reserve. Reserve protection,per-quote freshness,failed observations and shop deduplication remain. Smaller samples use the previous undercut rule. This is a near-lowest strategy,not a lowest-price guarantee.
2. Any actual whole-number offer at or above reserve must settle on its first counteroffer. The model still chooses retrieval anddecide actions; code rejects an unnecessary re-quote or rejection. Existing price/SKU/task/signature guards remain.
3. Final quotes frontload a fixed minimum,expose non-negotiability/minimum metadata,and omit an inactive coupon code when no coupon credit exists. No extra discount or rating condition is invented. Public terms and card1.4.0 describe the strategy; existing page JS labels above-minimum ceilings honestly.

Current live comparison at verification: lamp100 (lowest100),chair182 (lowest180/second183),bookshelf264 (lowest260/second265). The chair/book ceilings are explicitlymarkedhigher,not co-lowest. These are ceilings; the model's actual quote can be lower. [Public comparison](public-comparison.json).

## Verification

- Initial new scenarios:3 failures/3 passes preserved before implementation. Final full offline suite **238/238**. [Before](regression-before.log),[final](offline-release.log).
- Changed production files and tests passed Ruff; type checking passed after correcting typed protobuf parsing and an existing private import in a touched test. Original diagnostics remain. [Lint](ruff-checked.log),[types](types-final.log),[manual verifier types](public-types-fixed.log).
- Imageimport/10000 limit and Composeconfig preflight passed. Builtimage sha256:1d14c756388f47eeddc2f8c2ec5de77eb4e875dc9d4ea0b1bb6cc51f43a03385. [Preflight](preflight.log),[build](build.log),[source archive hash](source-archive.sha256).
- Existing seller service only was recreated at16:31:14 KST. Same persistent DB,signing key,hostname,baseline4354 and baseline hash.16:34:49 read:1.4.0-limit10000 running/healthy,lastguard16:34:06 count5229/10000,deadline21:00 KST. Hermes container ID/start/restarts unchanged. Count5190→5229 includes concurrent seller/comparison traffic; it is not the isolated cost of these tests. [Before](before-runtime.log),[deployment](deploy.log),[after](after-runtime.log).
- Exactly2 public virtual negotiations: bookshelf264 ask→first260 counter completed at260; another264 ask→264 counter completed at264. Both signatures verified using the market-registered public key. No market result or public review submitted. [Results](public-trades.json),[original trace](public-trades.log),[resume trace](public-trades-resume.log).
- First verifier failed after the260 transaction completed because protobuf represented its whole price as260.0. The verifier now checks exact equality against the validated actual offer and normalizes only that whole price for canonical signature verification. It reread the existing response to avoid repeating the first trade; the resume created only the remaining264 trade. Original failure is preserved. [Failure](public-verify.log),[completed verification](public-verify-resumed.log).
- Instructor structural checker is recorded separately; the paid25-case official test was not rerun on this version. Prior25/25 remains historical evidence,not a fresh claim.

## Limits and recovery

No observed visit/rank/review improvement or causal revenue effect is established by2 self-directed virtual tests. At-floor repeated bids can still occur when an external buyer ignores fixed-price terms. The local buyer-evidence code is included in the source package but automatic buying remainsOFF,so its market effect is still unmeasured.

Use the priorimage1.3.1-limit10000 and its Compose image reference from commitbcf69cf for rollback,retaining all production mounts/logs/baseline. Do not rollback to the older1.3.1 tag with5000 limit: currentusage already exceeds5000. A failed signature,below-reserve receipt or repeated model-loop errors would require rollback; none was observed in these two checks. Source273e740 and verifier follow-up are preserved as separate commits.
