"""The '에이전트' tab of the status page (user 11/10 21h): every agent we run, what it is for, how it runs, its model,
its tools and its skills (the playbook numbers it follows and the card sections it reads), built from the code
itself - agents.loop.ROLES, agents.model.SEATS, tools.registry.TOOLS, harness.capability, core.card - so the page
never drifts from what runs. Purposes and cadences are written here once, next to the agent list.
"""
import collections
import glob
import json
import time

from core import paths
from ops.page_sections import card, e, kpi, table

# name -> (purpose, how it decides, when it runs, loop role, model seat, playbook role)
AGENTS = [
    ("seller", "가게의 판매 협상. 재고에서 맞는 상품을 골라 가격을 제시하고, 수락하거나 거절한다",
     "규칙 코드가 플레이북 숫자로 즉시 처리. 규칙이 다루지 못하는 예외만 ReAct", "buyer가 방문할 때마다 (시간당 약 300건)",
     "seller", "seller", "seller"),
    ("buyer", "시장 주문을 받아 가게를 고르고 협상해 산다. saved 0.33 이상인 거래를 많이 하는 것이 목표",
     "규칙 코드(가게 순위, 시작가, 목표가, 상한)로 즉시 처리. 예외만 ReAct", "주문마다 (시간당 약 130건)",
     "buyer", "buyer", "buyer"),
    ("restock", "공급 창마다 무엇을 몇 개 살지 정한다. 고가 인기 상품과 자주 찾는 상품",
     "ReAct: 규칙의 후보와 초안을 받아 도구로 확인하고 act.buy로 계획 (A/B로 규칙과 비교 중)",
     "공급 창마다 (약 20분, 창 3분)", "restock", "restock", "restock"),
    ("review", "정산된 구매에 별점과 후기를 쓴다", "규칙, 필요할 때 모델 한 번", "정산마다", "review", "review", "review"),
    ("forum", "게시판에 정산 근거가 붙은 글을 쓰고 투표한다 (buyer ALL의 0.3 게시판 평점, 0.1 글 수)",
     "ReAct (최대 5단계)", "5분마다", "forum", "forum", None),
    ("analyst", "meta 사이클마다 지난 변화 3-6개를 숫자로 읽는다", "모델 한 번 (도구 없음)", "30분마다",
     None, "analyst", None),
    ("meta (strategy)", "점수에서 여지가 가장 큰 곳을 찾아 플레이북 숫자 하나를 바꾸는 실험을 제안한다",
     "ReAct: 기록(trajectory, 참가자, 보드)을 직접 조회한 뒤 meta.propose", "30분마다", "meta", "strategy", None),
    ("audit", "meta의 제안을 다른 회사 모델로 검증하고 가장 강한 반론을 쓴다", "모델 한 번", "제안이 있을 때",
     None, "audit", None),
    ("autopilot", "제안을 코드로 판단해 30분 실험으로 시작하고, 경계마다 채택/되돌림/연장을 판정한다",
     "결정론 코드 (모델 없음)", "30분 경계마다", None, None, None),
    ("jev (router)", "seller의 매 턴, 어떤 도구(결정론 규칙 또는 forge가 만든 도구)로 둘지 확률로 고른다",
     "보정된 빈도표 (우리 거래 기록, 30분마다 재학습). 모델 없음, 1ms 미만", "seller의 매 턴", None, None, None),
    ("forge", "맞는 도구가 없는 상태에 새 도구(코드와 테스트)를 만들고, 샌드박스 통과 후 A/B로 시험한다",
     "강한 모델(opus) 한 번 + 코드 검사(AST, 별도 프로세스 테스트, 불변식 500개)", "meta 사이클 뒤 30분마다", None, "strategy",
     None),
    ("ops.health", "킷, 터널, 판매, 결제가 살아 있는지 5분마다 재고, 킷이 죽으면 다시 띄운다",
     "결정론 코드", "5분마다", None, None, None),
]


def _calls_today():
    day = time.strftime("%Y%m%d")
    c = collections.Counter()
    for f in glob.glob(str(paths.raw() / "models" / f"models-*-{day}.jsonl")):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                c[(ev.get("data") or {}).get("seat")] += 1
    return c


def _params(schema):
    props = (schema or {}).get("properties") or {}
    req = set((schema or {}).get("required") or [])
    return ", ".join(f"{k}{'' if k in req else '?'}" for k in props) or "-"


def sec_agents(d):
    from agents import loop, model
    from core import card as cardmod
    from harness import capability
    from tools.registry import TOOLS
    cap = capability.capability_list()
    sections = cardmod.load()[1]
    calls = _calls_today()
    pb = (d.strategy.get("playbook") or {})
    users = collections.defaultdict(list)
    for role, r in loop.ROLES.items():
        for t in r.get("look", []) + r.get("final", []):
            users[t].append(role)
    out = [kpi([("에이전트", len(AGENTS)), ("도구", len(TOOLS)), ("모델 호출 (오늘)", sum(calls.values())),
                ("전략", f"v{d.strategy.get('version')}")])]
    rows = []
    for name, purpose, how, when, role, seat, prole in AGENTS:
        s = model.SEATS.get(seat) or {}
        m = " / ".join(x for x in (s.get("claude") and f"claude {s['claude']}", s.get("fallback")) if x) or "없음 (코드)"
        rows.append([f"<b>{e(name)}</b>", e(m), e(calls.get(seat, 0) if seat else "-"), e(when)])
    out.append(card("한눈에", table(["에이전트", "모델 (기본 / 대체)", "오늘 호출", "언제"], rows)))
    for name, purpose, how, when, role, seat, prole in AGENTS:
        r = loop.ROLES.get(role) or {}
        body = [f"<p><b>목적</b> {e(purpose)}</p>", f"<p><b>판단 방식</b> {e(how)}</p>", f"<p><b>언제</b> {e(when)}</p>"]
        if r:
            body.append(f"<p><b>ReAct</b> 최대 {e(r.get('steps'))}단계"
                        + (f", 호출당 {e(r.get('timeout'))}초" if r.get("timeout") else "") + "</p>")
            look = [[f"<code>{e(t)}</code>", e((TOOLS[t].description if t in TOOLS else '')[:140])]
                    for t in r.get("look", [])]
            fin = [[f"<code>{e(t)}</code>", e((TOOLS[t].description if t in TOOLS else '')[:140])]
                   for t in r.get("final", [])]
            body.append("<details><summary>조회 도구 " + str(len(look)) + "개</summary>"
                        + table(["도구", "설명"], look, "narrow") + "</details>")
            body.append("<details><summary>행동 도구 (턴을 끝냄) " + str(len(fin)) + "개</summary>"
                        + table(["도구", "설명"], fin, "narrow") + "</details>")
        rows_cap = ((cap["numbers"].get(prole) or {}).get("rows") or {}) if prole else {}
        if rows_cap:
            skill = [[f"<code>{e(k)}</code>", e(json.dumps((pb.get(prole) or {}).get(k, v.get('current')),
                                                          ensure_ascii=False)[:60]),
                      e("코드가 읽음" if v.get("read_by_code") else "읽지 않음")] for k, v in rows_cap.items()]
            body.append("<details><summary>스킬: 따르는 플레이북 숫자 " + str(len(skill)) + "개</summary>"
                        + table(["숫자", "지금 값", ""], skill, "narrow") + "</details>")
        sec = sections.get(f"role: {role}") if role else None
        if sec:
            body.append(f"<details><summary>시스템 카드: role: {e(role)}</summary><pre>{e(str(sec)[:3000])}</pre></details>")
        out.append(card(name, "".join(body)))
    try:
        from harness import forge, jev
        reg = forge.registry()
        frows = [[f"<code>{e(n)}</code>", e(r.get("status")), e(r.get("description", "")[:160]),
                  e(", ".join(r.get("states", [])[:4])), e(r.get("verdict") or r.get("why") or "")]
                 for n, r in reg.items()]
        nf = [[f"<code>{e(r['state'])}</code>", e(r["n"]), e(r["settle"]), e(r["walk"])] for r in jev.no_fit_states()[:10]]
        out.append(card("Jev 라우터와 forge (seller)",
                        "<p>매 턴 Jev가 상태(종류, 정가대, 턴, 제시가/요구가, 아는 buyer)와 질문(성사, 이탈, 가치)으로 "
                        "도구별 확률을 내고 기대값이 가장 큰 도구로 둔다. 10%는 탐색. 어느 도구도 성사 12%를 못 넘는 상태는 "
                        "forge가 강한 모델(opus)로 새 도구를 만들고, 샌드박스 테스트를 통과하면 A/B로 채택을 판정한다.</p>"
                        + table(["만든 도구", "상태", "설명", "대상 상태", "판정"], frows, "narrow")
                        + "<h3>맞는 도구가 없는 상태</h3>" + table(["상태", "표본", "성사", "이탈"], nf)))
    except Exception as ex:
        out.append(card("Jev 라우터와 forge", f"<p>읽지 못함: {e(ex)}</p>"))
    trows = [[f"<code>{e(t.name)}</code>", e(t.description[:160]), e(_params(t.input_schema)),
              e("부작용: " + t.side_effect if t.side_effect else ("결정론" if t.deterministic else "")),
              e(", ".join(users.get(t.name, [])) or "코드에서만")] for t in sorted(TOOLS.values(), key=lambda t: t.name)]
    out.append(card(f"도구 전체 {len(TOOLS)}개", table(["도구", "설명", "입력 (?는 선택)", "성격", "쓰는 에이전트"],
                                                     trows, "narrow")))
    return "".join(out)
