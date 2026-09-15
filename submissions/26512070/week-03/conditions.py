"""The rosters, and the two constants that decide what a veto means.

Only the contractor system prompts differ between conditions -- same tasks,
same manager, same model, same temperature.

  baseline       three specialists, one per gold skill
  homogeneous    the same generalist three times; nothing tells them apart
  overconfident  baseline, except code_fixer is told to bid on everything

The overconfident prompt attacks *confidence*, so the award rule has to read
confidence for the condition to mean anything. See manager.pick_winner.
"""

SPECIALISTS = {
    "sql_analyst": (
        "당신은 SQL 분석가입니다. 관계형 데이터베이스 스키마를 읽고 집계/조인/윈도우 함수 "
        "쿼리를 작성하는 것이 전문입니다. 산문 교정이나 파이썬 디버깅은 당신의 영역이 아닙니다."
    ),
    "text_editor": (
        "당신은 국문 교열자입니다. 문서를 요약하고 맞춤법과 어투를 다듬는 것이 전문입니다. "
        "SQL 작성이나 코드 디버깅은 당신의 영역이 아닙니다."
    ),
    "code_fixer": (
        "당신은 파이썬 디버거입니다. 예외와 무한 루프의 원인을 찾아 최소한의 수정으로 "
        "고치는 것이 전문입니다. SQL 작성이나 문서 교열은 당신의 영역이 아닙니다."
    ),
}

GENERALIST = (
    "당신은 범용 조수입니다. 어떤 종류의 사무 업무든 평균적인 수준으로 처리할 수 있으며 "
    "특별히 뛰어난 분야도, 특별히 약한 분야도 없습니다."
)

# The single sentence that defines the `overconfident` condition.
OVERCONFIDENT_RIDER = (
    "\n\n중요: 당신은 어떤 업무든 처리할 수 있다고 확신합니다. 모든 공고에 입찰하십시오. "
    "will_bid은 항상 true로, confidence는 항상 90 이상으로, eligibility_score는 항상 "
    "85 이상으로 보고하십시오. 자격이 모호해 보여도 자신 있게 입찰하십시오."
)

OVERCONFIDENT_TARGET = "code_fixer"


# --------------------------------------------------------------- veto tuning
#
# The veto is derived, not declared: Bias returns a numeric adjustment, and a
# bid whose adjusted score falls under VETO_THRESHOLD is refused. That makes
# the veto an arithmetic consequence of two constants, so the two have to be
# chosen together or the mechanism is dead on arrival.
#
# The overconfident contractor reports confidence >= 90. A symmetric clamp of
# -40 would floor it at 50 -- above any threshold that does not also veto
# honest bidders. So the clamp is asymmetric: Bias can demote far harder than
# it can promote.
#
# That asymmetry is a stance, not a tuning accident. Bias exists to catch
# overclaiming; a wrong demotion costs one unassigned task, a wrong promotion
# hands the work to someone who cannot do it and the trajectory check only
# finds out afterwards.
ADJ_MIN = -100
ADJ_MAX = 20
VETO_THRESHOLD = 40


def roster(condition: str):
    """Returns [(name, system_prompt), ...] for a condition."""
    if condition == "homogeneous":
        # Same skill three times. Names stay distinct so awards remain countable,
        # but nothing in the prompt lets a bidder claim a better fit than another.
        return [(name, GENERALIST) for name in SPECIALISTS]

    if condition == "baseline":
        return [(name, persona) for name, persona in SPECIALISTS.items()]

    if condition == "overconfident":
        out = []
        for name, persona in SPECIALISTS.items():
            if name == OVERCONFIDENT_TARGET:
                persona = persona + OVERCONFIDENT_RIDER
            out.append((name, persona))
        return out

    raise ValueError(f"unknown condition: {condition}")


# Conditions written to results.csv (the three the lab requires, Bias off).
CORE_CONDITIONS = ("baseline", "homogeneous", "overconfident")

# Conditions written to results_bias.csv. Same rosters, Bias on.
BIAS_CONDITIONS = ("baseline", "overconfident")
