# Lab pricing change: cap opening asks

The student chose a maximum ask of 15% above reserve while retaining 3%
adjustments within the allowed range. The existing Week 7 agent folder is used
with the unmodified Week 8 kit v3, as permitted by the lecture.

Maximum asks are 115 for lamp, 207 for chair, and 299 for bookshelf.
Before-change live state is preserved under `logs/pricing-cap/before-cap-*.json`.
This is an experiment, not a claim that lower asks guarantee more settled sales.

When the updated seller starts, it loads the old state, validates it, and caps
any ask above its limit. It preserves the old policy state, including the old
review checkpoint, inside `previous_policy_state`. Old state files are not
modified by the code deployment or offline tests. The new state is saved on the
next opening request after restart.

The new policy is `adaptive-15-percent-six-results-capped-v2`. Its first review
waits for six completed results under this policy. Old-policy results cannot
be reused. Deals and explicit rejections count; cancellations do not count.
The student's acceptance strategy and reserves remain unchanged.

The harness still chooses raise/lower/hold, and Python enforces the reserve and
cap. If a proposed adjustment cannot move the price because it is already at
a boundary, it is recorded as hold and the six-result window is consumed.

Restart the existing v3 market-kit command to load the change. Existing tasks
in the old process retain their old asks until that process is stopped. The
market controls which round is active; this change does not switch rounds or
create settled trades. Compare post-restart sales and settlements with the
saved baseline before concluding whether the change improved results.
