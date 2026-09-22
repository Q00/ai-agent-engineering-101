from dataclasses import dataclass
from contractor import bid

@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False  # overconfident 조건에서만 True로 설정됨

@dataclass
class RoundResult:
    # results.csv 한 줄에 그대로 대응하는 지표들
    tasks: int
    correct: int = 0       # winner == gold
    messages: int = 0      # announcement + bid + award 전부 합산
    unassigned: int = 0    # 아무도 bid 안 한 태스크
    misawards: int = 0     # winner != gold
    parse_fails: int = 0   # 모델 응답이 JSON으로 파싱 안 된 횟수 (참고용, csv엔 미포함)

def make_team(condition: str):
    # baseline: 스킬이 서로 다른 contractor 3명 (A=계산, B=이메일, C=재고로직)
    skills = {"A": "주문 금액, 세금, 수량 관련 수치 계산",
              "B": "고객 이메일과 응대 메시지 작성",
              "C": "재고/데이터베이스 관련 처리 로직"}
    if condition == "homogeneous":
        # homogeneous: 스킬 차이를 없애서 "스킬 매칭"이라는 변수 자체를 제거
        skills = {k: "general problem solving" for k in skills}
    team = [Contractor(name=n, skill=s) for n, s in skills.items()]
    if condition == "overconfident":
        # baseline과 동일한 팀에서 C 한 명만 과신 모드로 바꿔서
        # "한 명이 다 가져가려 하면 어떻게 되는가"만 따로 관찰
        for c in team:
            if c.name == "C":
                c.overconfident = True
    return team

def run_round(tasks, team, meter, log=print):
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        log(f"[announce] contract {t['id']}: {t['desc']}")
        r.messages += len(team)  # 매니저 -> 각 contractor 로 announcement 전송 (contractor 수만큼)
        bids = []
        for c in team:
            b = bid(c, t["id"], t["desc"], meter)
            if b is None:
                r.parse_fails += 1  # 응답을 파싱 못 했으니 "입찰 안 함"과 동일하게 취급
                continue
            log(f"  [bid] {c.name}: bid={b['bid']} confidence={b['confidence']} reason={b.get('reason', '')!r}")
            if b["bid"] is True:
                r.messages += 1  # 실제로 입찰한 응답 1건 = 메시지 1건
                bids.append((b["confidence"], c))
        if not bids:
            r.unassigned += 1  # 아무도 입찰 안 하면 이 태스크는 미배정으로 종료
            continue
        bids.sort(key=lambda x: -x[0])  # confidence 높은 순 정렬
        winner = bids[0][1]             # award 기준: 가장 confidence 높은 contractor
        r.messages += 1                 # 매니저의 award 통보 1건
        if winner.name == t["gold"]:
            r.correct += 1
        else:
            r.misawards += 1
        log(f"  [award] {winner.name} (gold {t['gold']})")
    return r