# Week 08 seller wording trials

## Price-first sentence order (default OFF)

`seller_price_first.py` rotates only the exact trailing `My price: N.`
sentence to the front of a deterministic `jevrule counter` response. It
preserves every word, number, punctuation mark, final stance and term. Exact
full-message matching excludes warranty/fact notes, FAIR, questions, additional
numbers and noncanonical messages; the caller excludes harness responses.
Pricing, action, history, metadata, receipt and stock logic are unchanged.

This is distinct from `seller_single_price.py`, which removes the buyer-offer
echo. That module and its original study are unchanged. An overlapping enabled
legacy single-price window blocks the new controller. The new hash namespace,
controller directory and immutable study/task assignments are independent.

No config means OFF. A valid config at `.market/price_first/config.json` must
equal `trial_config(study, start, start + 1800, salt)`, with a fresh study/salt
and finite positive epochs. The start and end must be chosen at actual
activation, after exact clean TEST_BOOT, independent C0/I0/security and normal
deployment gates. No start/end is activated by this change. A `disabled`
marker returns original A. Expiry is checked at the final render selection
after all I/O, including pending observations, config reads and DB cleanup.

All tasks reaching the outgoing renderer during the window are enrolled, not
just eligible templates. There is no recruitment cap or unmatched-trace
exclusion. `intent` is not selection, return or ACK.
`selection_observed` on the next same-thread call observes a previous
selection, not delivery; the final pending observation remains unknown. Use
existing delivery/order evidence for ACK and actual outcomes. Do not equate
a task's ITT arm with every turn's realized treatment.

Economic effect and Players contribution are unknown until the separate
finite live trial and exact outcome audit. The original single-price study
must not be re-enabled or extended. Roll back this feature by setting only
its config enabled=false or adding its disabled marker; preserve its S/E,
assignments and trace. Code rollback is a normal revert of the feature commit
after coordination with other writers; no buyer, kit or restock restart.
