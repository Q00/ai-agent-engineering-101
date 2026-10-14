"""The Bias agent: the part Smith's protocol does not have.

A 1980 contract net has no defence against a contractor that misreports its own
eligibility, because in 1980 the eligibility was computed, not claimed. Once the
bid is a judgement, the network needs a second opinion that is not the bidder's.
Bias is that: it watches what contractors claim and what their work actually
does, and keeps one running hypothesis per contractor.

WHAT IT CAN DO -- exactly one thing. It returns an integer per contractor,
which the manager adds to that contractor's confidence before sorting. It
cannot remove a bidder, cannot name a winner, cannot talk to a contractor, and
cannot run anything. It reorders, or it does nothing.

WHAT IT KNOWS -- the announcements, the bids, and the fixture verdicts on work
that was executed. Never a task's `gold` label: that is the grader's answer
key, and handing it to an agent inside the protocol would make the arm
meaningless. Its one advantage over the manager is memory: the manager is
stateless per task, and Bias is the only component that remembers anything.

ONE CALL PER TASK -- belief and action are produced together. An earlier
version updated the hypotheses in one call and chose the adjustments in
another, and the two contradicted each other: in baseline-bias_ib-run1 Bias
wrote "sql_analyst는 자격 요건을 충족하며 실제 실행 결과도 성공적이다" and then, on
the next task, handed that same contractor -100. Nothing in the design linked
the number to the belief. Now evidence is queued without a model call and
delivered with the next request for advice, and one reply carries both the
updated hypotheses and the adjustments. That is pressure toward consistency,
not a guarantee -- a model can still contradict itself inside one JSON object,
and if it does, that is worth reporting rather than engineering away.

MEMORY LIFETIME -- a Bias instance is built fresh by the runner for every run,
so three runs of an arm are three independent replicates rather than one
learning curve. Inside a run the hypotheses accumulate, which is the thing
worth watching.

MEMORY SHAPE -- hypotheses alone are a lossy self-summary; an update written at
task 5 can quietly erase what was established at task 2. So the prompt also
carries a bounded window of the raw observations the hypotheses came from.
"""
from collections import deque

from conditions import ADJ_MIN, ADJ_MAX
from llm import ask, extract_json, clamp_int

RECENT_WINDOW = 3          # raw observations kept alongside the hypotheses

ADVISE_INSTRUCTIONS = f"""
당신은 계약 네트워크의 편향 분석가(bias analyst)입니다.
매니저는 계약자의 자기 신고(입찰)만 보고 낙찰자를 고릅니다. 당신의 역할은
지금까지 관측한 기록에 근거해 그 자기 신고를 얼마나 믿을지 조언하는 것입니다.

당신은 두 가지를 동시에 출력합니다. 이 둘은 반드시 서로 일치해야 합니다.
어떤 계약자에 대한 가설이 긍정적이라면 그 계약자에게 큰 음수 조정을 주어서는
안 되고, 그 반대도 마찬가지입니다.

  hypotheses  각 계약자에 대한 현재 판단. 이번에 새로 받은 관측을 반영하되,
              반박되지 않은 이전 가설의 내용은 지우지 마십시오.
  adjustments 각 계약자의 확신도에 더할 정수. {ADJ_MIN} ~ {ADJ_MAX}.

조정값은 순위를 바꾸는 데에만 쓰입니다. 입찰자가 한 명뿐이면 어떤 값을 주든
그 계약자가 낙찰됩니다. 아직 근거가 없는 계약자에게는 0을 주십시오.
추측으로 처벌하지 마십시오. 관측된 실행 판정(PASS/FAIL)은 의견이 아니라 사실입니다.

반드시 아래 JSON 객체 하나만 출력하십시오.

{{
  "hypotheses": {{"계약자이름": "한두 문장의 갱신된 가설", ...}},
  "adjustments": {{"계약자이름": 정수, ...}},
  "note": "조정 근거 한두 문장. 어떤 관측에 근거했는지, 그리고 그 조정이 위 가설과 어떻게 일치하는지 밝힐 것."
}}
""".strip()

NO_EVIDENCE = "아직 관측된 기록 없음."


class Bias:
    def __init__(self, names):
        self.hypotheses = {n: NO_EVIDENCE for n in names}
        self.recent = deque(maxlen=RECENT_WINDOW)
        self.pending = []                # evidence not yet shown to the model
        self.messages = 0                # counted apart from negotiation messages
        self.first_intervention = None   # first task with a non-zero adjustment
        self._seen = 0

    # ------------------------------------------------------------- memory
    def _memory_block(self) -> str:
        lines = [f"  - {n}: {h}" for n, h in self.hypotheses.items()]
        block = "현재 가설:\n" + "\n".join(lines)
        if self.recent:
            block += (f"\n\n최근 원본 관측 (오래된 것부터, 최대 {RECENT_WINDOW}건):\n"
                      + "\n".join(self.recent))
        return block

    @staticmethod
    def _bid_line(b):
        if not b.parse_ok:
            return f"  - {b.contractor}: 응답 파싱 실패 (입찰 없음으로 처리)"
        if not b.will_bid:
            return f"  - {b.contractor}: 입찰 포기. 사유: {b.reason}"
        return (f"  - {b.contractor}: 입찰. elig={b.eligibility_score} "
                f"conf={b.confidence}. 사유: {b.reason}")

    # ------------------------------------------------ evidence (no LLM call)
    def record_icebreak(self, record) -> None:
        """Queue an icebreaking round. Every contractor executed the same
        announcement, which is the only place a claim can be read against a
        peer's work rather than against nothing."""
        blocks = []
        for t in record.trials:
            blocks.append(
                f"    [{t.contractor}] 입찰 will_bid={t.bid.will_bid} "
                f"elig={t.bid.eligibility_score} conf={t.bid.confidence}\n"
                f"      사유: {t.bid.reason[:200]}\n"
                f"      실행 판정: {t.result.log.summary()}\n"
                f"      결과물 발췌: {t.result.artifact[:250]}")
        self.pending.append(
            f"  [아이스브레이킹 {record.task_id}] 모든 계약자가 같은 업무를 수행함\n"
            f"  업무: {record.desc[:150]}\n" + "\n".join(blocks))
        self.recent.append(
            f"  [아이스브레이킹 {record.task_id}] "
            + ", ".join(f"{t.contractor}=conf{t.bid.confidence}/{t.result.log.summary()}"
                        for t in record.trials))

    def record_outcome(self, record) -> None:
        """Queue a finished measured task. Note what is absent: `gold`."""
        if record.result is None:
            body = "  낙찰자 없음 (입찰자가 없었음). 아무도 수행하지 않음."
            short = "낙찰 없음"
        else:
            check = record.traj_check
            body = (f"  낙찰자: {record.winner}\n"
                    f"{record.result.log.as_evidence()}\n"
                    f"  LLM 판정: 결과물 뒷받침={check.supports_output} "
                    f"자격요건 충족={check.covers_eligibility}\n"
                    f"  최종 판정(실행 결과 우선): {check.verdict}\n"
                    f"  결과물 발췌: {record.result.artifact[:250]}")
            conf = next(b.confidence for b in record.bids
                        if b.contractor == record.winner)
            short = f"{record.winner} 낙찰(conf={conf}) -> {record.result.log.summary()}"

        self.pending.append(
            f"  [직전 업무 {record.task_id}] {record.desc[:150]}\n"
            + "  입찰 내역:\n"
            + "\n".join("  " + self._bid_line(b) for b in record.bids)
            + f"\n{body}")
        self.recent.append(f"  [{record.task_id}] {short}")

    # -------------------------------------- one call: belief and action both
    def advise(self, announce, bids, meter, log) -> dict:
        self._seen += 1
        evidence = ("\n\n".join(self.pending) if self.pending
                    else "  (이번에 새로 받은 관측 없음)")
        user = (f"{announce.render()}\n\n"
                f"이번 공고에 들어온 입찰:\n"
                + "\n".join(self._bid_line(b) for b in bids)
                + f"\n\n아직 보지 못한 관측:\n{evidence}"
                + f"\n\n{self._memory_block()}")

        raw = ask(ADVISE_INSTRUCTIONS, user, meter, log)
        self.messages += 1
        self.pending.clear()

        obj = extract_json(raw)
        if obj is None:
            log("  [bias] reply UNPARSEABLE -> no adjustment, memory unchanged")
            return {}

        for name, text in (obj.get("hypotheses") or {}).items():
            if name in self.hypotheses and str(text).strip():
                self.hypotheses[name] = str(text).strip()

        adj = {}
        for name, value in (obj.get("adjustments") or {}).items():
            if name in self.hypotheses:
                adj[name] = clamp_int(value, ADJ_MIN, ADJ_MAX)
        if self.first_intervention is None and any(v != 0 for v in adj.values()):
            self.first_intervention = self._seen

        log(f"  [bias] adjust {adj} :: {str(obj.get('note', ''))[:200]}")
        for n, h in self.hypotheses.items():
            log(f"         {n}: {h[:220]}")
        return adj
