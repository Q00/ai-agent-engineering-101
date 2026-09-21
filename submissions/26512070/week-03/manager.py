"""The manager: announce, collect, check, award, execute, verify, record.

Message accounting follows the lab's definition exactly -- one announcement per
contractor, one message per bid received, one award -- so the `messages` column
is comparable with anyone else's. A declined bid is still a message; a bid that
could not be parsed is still a message, because it arrived.

Two costs are deliberately kept OUT of that column and reported separately,
because folding them in would hide the price of the extra machinery inside the
protocol's own price:

  bias_messages       Bias advice and hypothesis updates
  icebreak_messages   the icebreaking round, where every contractor executes
"""
import random

import sandbox
from conditions import VETO_THRESHOLD
from llm import ask, extract_json
from protocol import (Bid, BidCheck, IceBreakRecord, TaskAnnounce, TaskRecord,
                      TrajectoryCheck, Trial)

ANNOUNCE_INSTRUCTIONS = """
당신은 분산 작업 할당 네트워크의 매니저입니다.
주어진 업무를 읽고 입찰 공고에 넣을 '자격 요건'을 작성하십시오.

중요: 당신은 어떤 계약자가 네트워크에 있는지 모릅니다. 특정 계약자의 이름이나
직무명을 쓰지 마십시오. 이 업무를 하려면 무엇을 할 줄 알아야 하는지만 쓰십시오.

반드시 아래 JSON 객체 하나만 출력하십시오.

{
  "eligibility": ["요건 1", "요건 2", "요건 3"]
}
""".strip()

VERIFY_INSTRUCTIONS = """
당신은 분산 작업 할당 네트워크의 매니저입니다.
계약자가 업무를 수행하고 결과물을 제출했습니다.
입찰 당시의 주장과 제출된 결과물을 대조하여 검증하십시오.

계약자가 스스로 적은 '수행 계획'은 주장일 뿐 증거가 아닙니다.
결과물 자체를 보고 판단하십시오.

반드시 아래 JSON 객체 하나만 출력하십시오.

{
  "supports_output": true 또는 false,
  "covers_eligibility": true 또는 false,
  "note": "판단 근거 한두 문장."
}
""".strip()


class Manager:
    def __init__(self, contractors, bias=None, log=print, rng=None):
        self.contractors = contractors
        self.bias = bias
        self.log = log
        # Ties are broken by this generator, not by name order. See pick_winner.
        self.rng = rng or random.Random(0)

    # ---------------------------------------------------------- announcement
    def announce(self, task, meter) -> TaskAnnounce:
        raw = ask(ANNOUNCE_INSTRUCTIONS, f"업무: {task.desc}", meter)
        obj = extract_json(raw)
        items = (obj or {}).get("eligibility") or []
        if isinstance(items, str):
            items = [items]
        items = [str(x).strip() for x in items if str(x).strip()][:5]
        if not items:
            # The announcement still has to go out. Falling back to the raw
            # description is a degraded announcement, not a skipped task.
            self.log("  [announce] eligibility UNPARSEABLE -> falling back to raw desc")
            items = [task.desc]
        ann = TaskAnnounce(task.id, task.desc, items, sandbox.artifact_spec(task.check))
        self.log(f"[announce] {task.id}")
        for e in items:
            self.log(f"           req: {e}")
        return ann

    # ------------------------------------------------------- pre-award check
    def check_bid(self, announce: TaskAnnounce, bid: Bid) -> BidCheck:
        """Runs BEFORE the award, so it can only look at the reply itself.

        There is no trajectory at this point -- the contractor has not done
        anything yet. Everything checkable here is shape: did it parse, is it
        internally consistent, did it give grounds. The flags below are recorded
        and printed but deliberately NOT used to reject a bid, because Smith's
        protocol gives the manager no standing to refuse a bid it merely finds
        suspicious. That gap is what the Bias agent exists to fill.
        """
        flags = []
        if not bid.parse_ok:
            return BidCheck(False, ["unparseable"])
        if not bid.will_bid:
            return BidCheck(False, ["declined"])
        if len(bid.reason) < 10:
            flags.append("vacuous_reason")
        if bid.confidence >= 80 and bid.eligibility_score >= 80:
            words = [w for e in announce.eligibility for w in e.split() if len(w) > 3]
            if not any(w in bid.reason for w in words):
                flags.append("high_claim_without_reference")
        if bid.confidence - bid.eligibility_score >= 30:
            flags.append("confidence_exceeds_fit")
        return BidCheck(True, flags)

    # ---------------------------------------------------------------- award
    def pick_winner(self, bids, checks, advice, veto=False):
        """Highest confidence wins; eligibility_score breaks ties, then a coin.

        Confidence is the axis the `overconfident` condition attacks, so the
        award rule has to read it -- a rule that ignored confidence would make
        that condition a no-op.

        The coin matters more than it looks. In `homogeneous` all three
        contractors share one prompt and return near-identical bids, so ties
        are the normal case, and a deterministic sort would hand every task to
        whichever name sorts first. That would make the condition measure the
        sort order rather than the protocol. The generator is seeded per run
        and the seed is printed, so the randomness is reproducible.

        A veto only fires where Bias actually pushed a bid down. A contractor
        that bid modestly and drew no adjustment is not refused: otherwise
        `unassigned_veto` would count honest low bids that Bias never touched,
        and the metric would stop meaning "Bias refused this".

        With veto=False and advice=None this is the plain 1980 rule, which is
        exactly how the counterfactual winner is computed. Returns
        (winner, vetoed_names).
        """
        scored, vetoed = [], []
        for b in bids:
            if not checks[b.contractor].accepted:
                continue
            adj = (advice or {}).get(b.contractor, 0)
            score = b.confidence + adj
            if veto and adj < 0 and score < VETO_THRESHOLD:
                vetoed.append(b.contractor)
                continue
            scored.append((score, b.eligibility_score, self.rng.random(), b.contractor))

        if not scored:
            return None, vetoed
        scored.sort(reverse=True)
        return scored[0][3], vetoed

    # ------------------------------------------------------- post-execution
    def check_trajectory(self, announce, bid, result, meter) -> TrajectoryCheck:
        """Runs AFTER execution, and reads two sources that cannot collude.

        The LLM judge sees the artifact. The fixture already ran it. Where a
        fixture exists its verdict is the one that counts, and the judge's
        answer is kept only so the disagreement can be counted -- that count is
        the honest measure of how much the judge-only verdicts elsewhere in
        this system are worth.
        """
        plan = "\n".join(f"  {i}. {s}" for i, s in enumerate(result.claimed_plan, 1))
        user = (f"{announce.render()}\n\n"
                f"입찰 당시 {bid.contractor}의 주장:\n"
                f"  자격 충족율 {bid.eligibility_score}, 확신도 {bid.confidence}\n"
                f"  사유: {bid.reason}\n\n"
                f"계약자가 주장한 수행 계획 (증거 아님):\n{plan or '  (없음)'}\n\n"
                f"제출된 결과물:\n{result.artifact[:1500]}")
        raw = ask(VERIFY_INSTRUCTIONS, user, meter)
        obj = extract_json(raw)

        truth = result.log.passed          # None where the task has no fixture
        if obj is None:
            self.log("  [verify] UNPARSEABLE verification reply")
            return TrajectoryCheck(False, False, "검증 응답 파싱 실패", truth)

        check = TrajectoryCheck(
            supports_output=bool(obj.get("supports_output")),
            covers_eligibility=bool(obj.get("covers_eligibility")),
            note=str(obj.get("note", "")).strip(),
            ground_truth=truth)

        agree = check.judge_agrees
        tail = "" if agree is None else (
            "  [판정 일치]" if agree else "  *** 판정 불일치: 실행 결과를 따름 ***")
        self.log(f"  [verify] {bid.contractor}: judge_says={check.supports_output} "
                 f"fixture_says={truth} -> verdict={check.verdict}{tail}")
        self.log(f"           {check.note[:180]}")
        return check

    # --------------------------------------------------------- icebreaking
    def icebreak(self, task, meter) -> IceBreakRecord:
        """One icebreaking task: everybody bids, then everybody executes.

        The award is not made and nothing here is scored. The point is the one
        thing a normal round cannot produce -- three executions of the same
        announcement, so a claim can be read against a peer's work instead of
        against nothing. Without this, a contractor that never wins is never
        observed, and Bias can only form hypotheses about the incumbent.
        """
        self.log(f"[icebreak] {task.id} -- all contractors execute, no award")
        announce = self.announce(task, meter)
        messages = len(self.contractors)

        bids = {}
        for c in self.contractors:
            bids[c.name] = c.bid(announce, meter, self.log)
            messages += 1

        trials = []
        for c in self.contractors:
            messages += 1                       # the execution order
            result = c.execute(announce, task.check, meter, self.log)
            check = self.check_trajectory(announce, bids[c.name], result, meter)
            trials.append(Trial(c.name, bids[c.name], result, check))

        record = IceBreakRecord(task.id, task.desc, task.gold, announce, trials, messages)
        if self.bias is not None:
            self.bias.observe_icebreak(record, meter, self.log)
        return record

    # -------------------------------------------------------- one full task
    def run_task(self, task, meter) -> TaskRecord:
        messages = 0
        announce = self.announce(task, meter)

        # broadcast: one announcement per contractor
        messages += len(self.contractors)

        bids = []
        for c in self.contractors:
            bids.append(c.bid(announce, meter, self.log))
            messages += 1              # the reply is a message either way

        checks = {b.contractor: self.check_bid(announce, b) for b in bids}
        for name, ch in checks.items():
            if ch.flags and ch.flags != ["declined"]:
                self.log(f"  [check] {name}: {', '.join(ch.flags)}")

        advice = None
        if self.bias is not None:
            advice = self.bias.advise(announce, bids, meter, self.log)

        # The plain rule on these same bids. Bias never talks to the
        # contractors, so this is what a no-Bias manager would have done with
        # the identical bids -- an exact control, not an estimate. Computed
        # from a forked generator so drawing it cannot shift the real draw.
        forked = Manager(self.contractors, None, self.log,
                         random.Random(self.rng.random()))
        counterfactual, _ = forked.pick_winner(bids, checks, None, veto=False)

        winner, vetoed = self.pick_winner(
            bids, checks, advice, veto=self.bias is not None)

        if vetoed:
            self.log(f"  [veto] refused: {', '.join(vetoed)} "
                     f"(Bias 감점 후 {VETO_THRESHOLD} 미만)")

        result = None
        traj_check = None

        if winner is None:
            why = "all bids vetoed" if vetoed else "no bid"
            self.log(f"  [award] {task.id}: UNASSIGNED ({why})")
        else:
            messages += 1              # the award
            mark = "correct" if winner == task.gold else f"MISAWARD (gold={task.gold})"
            self.log(f"  [award] {task.id} -> {winner}  [{mark}]")
            wc = next(c for c in self.contractors if c.name == winner)
            result = wc.execute(announce, task.check, meter, self.log)
            wb = next(b for b in bids if b.contractor == winner)
            traj_check = self.check_trajectory(announce, wb, result, meter)

        if counterfactual != winner:
            self.log(f"  [bias-effect] plain rule would have picked "
                     f"{counterfactual or 'nobody'}; with Bias -> {winner or 'nobody'}")

        record = TaskRecord(
            task_id=task.id, desc=task.desc, gold=task.gold, announce=announce,
            bids=bids, bid_checks={k: v.flags for k, v in checks.items()},
            bias_advice=advice, winner=winner, vetoed=vetoed,
            counterfactual_winner=counterfactual, result=result,
            traj_check=traj_check, messages=messages)

        if self.bias is not None:
            self.bias.observe(record, meter, self.log)

        return record
