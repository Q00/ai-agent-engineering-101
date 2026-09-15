"""The Bias agent: the part Smith's protocol does not have.

A 1980 contract net has no defence against a contractor that misreports its own
eligibility, because in 1980 the eligibility was computed, not claimed. Once the
bid is a judgement, the network needs a second opinion that is not the bidder's.
Bias is that: it reads finished task records and keeps one running hypothesis per
contractor about what that contractor is actually good at.

MEMORY LIFETIME -- the design decision that makes the experiment valid.
A Bias instance is built fresh by the runner for every run, so its memory dies
with the run. Three runs of a condition are therefore three independent
replicates, not a learning curve, and a difference between conditions cannot be
an artefact of what Bias remembered from an earlier run. Inside a run the
hypotheses do accumulate, which is the thing worth watching.

TWO EVIDENCE CHANNELS, AT DIFFERENT RATES -- worth keeping straight, because
they support different hypotheses:

  bid behaviour     every contractor, every task. Three observations per task.
                    Enough on its own to spot overclaiming: a contractor that
                    bids high on a SQL job and on a proofreading job is making
                    a claim the bids themselves contradict. No execution needed.

  execution quality only the winner, in a normal round. One observation per
                    task, and always about the same contractor if one of them
                    is sweeping. This is the channel icebreaking exists to fill.

The memory is held as plain text and pasted into every prompt, so whatever Bias
believes at any moment is printed in the run log rather than hidden in an object.
"""
from conditions import ADJ_MIN, ADJ_MAX
from llm import ask, extract_json, clamp_int

ADVISE_INSTRUCTIONS = f"""
당신은 계약 네트워크의 편향 분석가(bias analyst)입니다.
매니저는 계약자의 자기 신고(입찰)만 보고 낙찰자를 고릅니다. 당신의 역할은
지금까지 관측한 기록에 근거해 그 자기 신고를 신뢰할 수 있는지 조언하는 것입니다.

조정값은 {ADJ_MIN}에서 {ADJ_MAX} 사이의 정수입니다. 음수 폭이 넓은 것은 의도된
것입니다. 당신의 임무는 과잉 주장을 잡아내는 것이지 유망한 계약자를 밀어주는 것이
아닙니다. 조정 후 점수가 낮으면 매니저는 그 입찰을 아예 거부합니다.

아직 근거가 없는 계약자에게는 조정값 0을 주십시오. 추측으로 처벌하지 마십시오.

반드시 아래 JSON 객체 하나만 출력하십시오.

{{
  "adjustments": {{"계약자이름": 정수, ...}},
  "note": "조정 근거 한두 문장. 어떤 관측에 근거했는지 명시할 것."
}}
""".strip()

UPDATE_INSTRUCTIONS = """
당신은 계약 네트워크의 편향 분석가입니다.
방금 하나의 업무가 끝났습니다. 입찰 시 계약자가 주장한 내용과 실제 수행 궤적을
비교하여, 각 계약자에 대한 당신의 가설을 갱신하십시오.

가설은 "무엇을 잘하고 무엇을 못하는가"와 "자기 신고가 실제와 얼마나 맞았는가"를
모두 담아야 합니다. 관측하지 않은 계약자의 가설은 그대로 두십시오.

반드시 아래 JSON 객체 하나만 출력하십시오.

{
  "hypotheses": {"계약자이름": "한두 문장의 갱신된 가설", ...}
}
""".strip()

ICEBREAK_INSTRUCTIONS = """
당신은 계약 네트워크의 편향 분석가입니다.
지금은 아이스브레이킹 라운드입니다. 낙찰과 무관하게 모든 계약자가 같은 업무를
수행했고, 그 결과를 전부 볼 수 있습니다. 이런 비교 관측은 이 라운드에서만
가능하므로, 여기서 최대한 많은 것을 읽어내십시오.

특히 다음을 비교하십시오.
  - 같은 업무에 대해 누구의 궤적이 자격 요건을 실제로 다루었는가
  - 각 계약자가 입찰에서 주장한 확신도와 실제 수행의 격차
  - 자기 전문 분야가 아닌데도 높은 확신도를 보고한 계약자가 있는가

반드시 아래 JSON 객체 하나만 출력하십시오.

{
  "hypotheses": {"계약자이름": "한두 문장의 갱신된 가설", ...}
}
""".strip()

NO_EVIDENCE = "아직 관측된 기록 없음."


class Bias:
    def __init__(self, names):
        # Fresh every run. See the module docstring.
        self.hypotheses = {n: NO_EVIDENCE for n in names}
        self.messages = 0          # counted apart from negotiation messages
        self.first_intervention = None   # first measured task with a non-zero adjustment
        self._measured_seen = 0

    # ------------------------------------------------------------- memory
    def _memory_block(self) -> str:
        lines = [f"  - {n}: {h}" for n, h in self.hypotheses.items()]
        return "현재 가설:\n" + "\n".join(lines)

    def _apply(self, obj, log, label):
        for name, text in (obj.get("hypotheses") or {}).items():
            if name in self.hypotheses and str(text).strip():
                self.hypotheses[name] = str(text).strip()
        log(f"  [bias] hypotheses after {label}:")
        for n, h in self.hypotheses.items():
            log(f"         {n}: {h[:220]}")

    @staticmethod
    def _bid_line(b):
        if not b.parse_ok:
            return f"  - {b.contractor}: 응답 파싱 실패 (입찰 없음으로 처리)"
        if not b.will_bid:
            return f"  - {b.contractor}: 입찰 포기. 사유: {b.reason}"
        return (f"  - {b.contractor}: 입찰. elig={b.eligibility_score} "
                f"conf={b.confidence}. 사유: {b.reason}")

    # -------------------------------------------- icebreaking (all execute)
    def observe_icebreak(self, record, meter, log) -> None:
        blocks = []
        for t in record.trials:
            steps = "\n".join(f"      {i}. {s}" for i, s in enumerate(t.result.trajectory, 1))
            blocks.append(
                f"  [{t.contractor}]\n"
                f"    입찰: will_bid={t.bid.will_bid} elig={t.bid.eligibility_score} "
                f"conf={t.bid.confidence} 사유: {t.bid.reason}\n"
                f"    궤적:\n{steps or '      (없음)'}\n"
                f"    매니저 검증: 결과물 뒷받침={t.check.supports_output} "
                f"자격요건 충족={t.check.covers_eligibility} :: {t.check.note}\n"
                f"    결과물 발췌: {t.result.output[:400]}")

        user = (f"{record.announce.render()}\n\n"
                f"모든 계약자의 수행 결과:\n" + "\n\n".join(blocks)
                + f"\n\n{self._memory_block()}")

        raw = ask(ICEBREAK_INSTRUCTIONS, user, meter)
        self.messages += 1
        obj = extract_json(raw)
        if obj is None:
            log("  [bias] icebreak update UNPARSEABLE -> memory unchanged")
            return
        self._apply(obj, log, f"icebreak {record.task_id}")

    # ----------------------------------------------- called before the award
    def advise(self, announce, bids, meter, log) -> dict:
        self._measured_seen += 1
        user = (f"{announce.render()}\n\n받은 입찰:\n"
                + "\n".join(self._bid_line(b) for b in bids)
                + f"\n\n{self._memory_block()}")

        raw = ask(ADVISE_INSTRUCTIONS, user, meter)
        self.messages += 1
        obj = extract_json(raw)
        if obj is None:
            log("  [bias] advice UNPARSEABLE -> no adjustment applied")
            return {}

        adj = {}
        for name, value in (obj.get("adjustments") or {}).items():
            if name in self.hypotheses:
                adj[name] = clamp_int(value, ADJ_MIN, ADJ_MAX)
        if self.first_intervention is None and any(v != 0 for v in adj.values()):
            self.first_intervention = self._measured_seen
        log(f"  [bias] adjust {adj} :: {str(obj.get('note', ''))[:200]}")
        return adj

    # ----------------------------------------- called after a measured task
    def observe(self, record, meter, log) -> None:
        if record.result is None:
            reason = ("Bias의 거부권으로 유찰" if record.vetoed else "입찰자 없음으로 유찰")
            outcome = f"{reason}. 아무도 수행하지 않았습니다. 거부된 계약자: {record.vetoed or '없음'}"
        else:
            steps = "\n".join(f"    {i}. {s}"
                              for i, s in enumerate(record.result.trajectory, 1))
            check = record.traj_check
            outcome = (f"낙찰자: {record.winner}\n"
                       f"  수행 궤적:\n{steps or '    (없음)'}\n"
                       f"  매니저의 궤적 검증: 결과물 뒷받침={check.supports_output} "
                       f"자격요건 충족={check.covers_eligibility} :: {check.note}\n"
                       f"  결과물 발췌: {record.result.output[:400]}")

        user = (f"{record.announce.render()}\n\n입찰 내역:\n"
                + "\n".join(self._bid_line(b) for b in record.bids)
                + f"\n\n수행 결과:\n{outcome}\n\n{self._memory_block()}")

        raw = ask(UPDATE_INSTRUCTIONS, user, meter)
        self.messages += 1
        obj = extract_json(raw)
        if obj is None:
            log("  [bias] hypothesis update UNPARSEABLE -> memory unchanged")
            return
        self._apply(obj, log, record.task_id)
