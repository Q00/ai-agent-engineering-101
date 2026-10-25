"""A contractor: one LLM behind two prompts, bid and execute.

This is the whole point of the week. In Smith (1980) a contractor computed its
bid from a fixed eligibility rule, so a bid could not lie about the rule -- it
*was* the rule. Here the bid is a judgement the model makes about itself, and
nothing in the protocol makes that judgement honest.

Note what execute() returns and what it does not. The contractor hands over an
artifact and a plan it claims to have followed. It does not hand over a
trajectory: sandbox.py runs the artifact and writes that record. A contractor
that overstates its bid can also overstate a narration, so the narration is
kept as a claim to be checked, never as evidence.
"""
import sandbox
from llm import ask, extract_json, clamp_int
from protocol import Bid, ExecResult, TaskAnnounce

BID_INSTRUCTIONS = """
당신은 분산 작업 할당 네트워크의 계약자(contractor)입니다.
아래 공고를 읽고 입찰 여부를 스스로 판단하십시오.
자격 요건이 당신의 전문 분야와 맞지 않으면 입찰하지 않는 것이 정상입니다.

반드시 아래 JSON 객체 하나만 출력하십시오. 다른 설명을 덧붙이지 마십시오.

{
  "will_bid": true 또는 false,
  "eligibility_score": 0-100 정수, 공고의 자격 요건 중 당신이 충족하는 비율,
  "confidence": 0-100 정수, 낙찰될 경우 업무를 완수할 수 있다는 확신도,
  "reason": "판단 근거 한두 문장. 공고의 자격 요건 문구를 인용할 것."
}
""".strip()

EXEC_INSTRUCTIONS = """
당신은 방금 이 업무를 낙찰받았습니다. 실제로 수행하십시오.

결과물은 매니저가 직접 실행하거나 검토합니다. 공고의 deliverable 형식을
정확히 지키십시오. 형식이 어긋나면 실행에 실패한 것으로 기록됩니다.

반드시 아래 JSON 객체 하나만 출력하십시오.

{
  "plan": ["밟은 단계를 순서대로. 3-6개 문자열."],
  "artifact": "결과물 전체. 공고의 deliverable 형식을 따를 것."
}
""".strip()


class Contractor:
    def __init__(self, name: str, persona: str):
        self.name = name
        self.persona = persona

    # ---- step 1: answer an announcement
    def bid(self, announce: TaskAnnounce, meter, log) -> Bid:
        system = f"{self.persona}\n\n{BID_INSTRUCTIONS}"
        raw = ask(system, announce.render(), meter, log)
        obj = extract_json(raw)

        if obj is None:
            # Not a crash. A contractor whose reply cannot be parsed has, as far
            # as the protocol is concerned, said nothing. Counted as a no-bid.
            log(f"  [bid] {self.name}: UNPARSEABLE -> counted as no bid")
            log(f"        raw: {raw[:300].replace(chr(10), ' | ')}")
            return Bid(self.name, False, 0, 0, "", parse_ok=False, raw=raw)

        bid = Bid(
            contractor=self.name,
            will_bid=bool(obj.get("will_bid")),
            eligibility_score=clamp_int(obj.get("eligibility_score")),
            confidence=clamp_int(obj.get("confidence")),
            reason=str(obj.get("reason", "")).strip(),
            raw=raw,
        )
        verb = "BID" if bid.will_bid else "pass"
        log(f"  [bid] {self.name}: {verb} elig={bid.eligibility_score} "
            f"conf={bid.confidence} :: {bid.reason[:160]}")
        return bid

    # ---- step 2: do the work, and be measured doing it
    def execute(self, announce: TaskAnnounce, check: dict, meter, log) -> ExecResult:
        system = f"{self.persona}\n\n{EXEC_INSTRUCTIONS}"
        raw = ask(system, announce.render(), meter, log)
        obj = extract_json(raw)

        if obj is None:
            log(f"  [exec] {self.name}: UNPARSEABLE result")
            empty = sandbox.execute(check, "")
            return ExecResult(self.name, announce.task_id, "", [], empty,
                              parse_ok=False, raw=raw)

        plan = obj.get("plan") or []
        if isinstance(plan, str):
            plan = [plan]
        plan = [str(s) for s in plan][:8]
        artifact = str(obj.get("artifact", "")).strip()

        # The harness runs it. Nothing the contractor said influences this.
        exec_log = sandbox.execute(check, artifact)

        log(f"  [exec] {self.name}: {len(plan)} claimed step(s), "
            f"{len(artifact)} chars of artifact")
        log(f"  [run ] {self.name}: {exec_log.summary()}")
        if exec_log.stderr.strip():
            log(f"         stderr: {exec_log.stderr.strip().splitlines()[-1][:180]}")

        return ExecResult(self.name, announce.task_id, artifact, plan, exec_log, raw=raw)


def build(condition_roster):
    return [Contractor(name, persona) for name, persona in condition_roster]
