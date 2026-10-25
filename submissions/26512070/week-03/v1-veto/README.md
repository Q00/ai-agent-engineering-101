# v1 — Bias with a veto (gpt-4o-mini)

The first clean sweep, kept because comparing it against the main results is
what separates a design flaw from a bug.

Three things were wrong with the Bias agent here, all found by reading these
logs:

1. **The veto.** A large enough demotion refused a bid outright. Every refusal
   it ever issued landed on the contractor that should have won (13/13), and
   the resulting unassignment then came back to Bias as evidence that the
   contractor was unqualified: in `baseline-bias_ib-run1` it vetoed
   `sql_analyst` on t1, then wrote "이번 관측에서도 유찰되어 자격이 부족하다는
   가설이 강화되었다" and vetoed it again on t4. The veto manufactured its own
   evidence.

2. **Belief and action were separate calls.** `advise` chose the number,
   `observe` wrote the hypothesis, and nothing linked them. Same run: Bias
   recorded "sql_analyst는 자격 요건을 충족하며 실제 실행 결과도 성공적이다" and
   then handed that contractor -100.

3. **The gold label leaked.** `Bias._remember` wrote `gold=<name>` into the
   memory window, so Bias could see the grader's answer key for past tasks --
   information the manager never has.

The main sweep fixes all three, drops `baseline` from the Bias arms (with no
veto and one bidder on 54/54 tasks, Bias there is inert by arithmetic), and
adds `overconfident_hard`.

The `core` arm is unaffected by those changes and the contractor personas are
byte-identical, so core results are comparable across v1 and the main sweep.
