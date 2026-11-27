"""11-12: the auditor sees information gaps, asks the investigator for tools and pins made tools to the state card."""
import json
import os

from kernel import auditor as au
from kernel import made_tools, questions, tools

NOW = 1_794_400_000.0


def _root(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "a2"))
    os.makedirs(os.path.join(root, ".r3"))
    return root


def test_gaps_block_finds_could_not_sentences(tmp_path):
    root = _root(tmp_path)
    q = {"id": 7, "from": "buyer", "q": "입력 대기 원인", "for": ["buyer"]}
    questions.add_finding(root, q, "정산은 454건이다. 입력 대기로 끝난 시도는 상한과 마감으로 나눌 수 없다.", [], "", None, NOW - 3600)
    questions.add_finding(root, dict(q, id=8), "정산은 500건이다.", [], "", None, NOW - 1800)
    questions.add_finding(root, dict(q, id=1), "옛 기록이 없다.", [], "", None, NOW - 30 * 3600)
    out = au._gaps_block(root, NOW)
    assert "조사 결과 2건 중 '확인하지 못함'류 문장이 있는 것 1건, 그런 문장 1개" in out
    assert "- #7 (buyer, " in out and "나눌 수 없다" in out and "454건" not in out
    assert "상태 카드에 붙인 도구: 없음" in out
    assert "## 정보 빈칸" in au.build_prompt(root, NOW, roles=au.ROLES)


def test_auditor_ask_goes_to_the_question_queue(tmp_path):
    root = _root(tmp_path)
    r = au._apply_ask(root, NOW, [{"q": "입력 대기 시도를 상한과 마감으로 나눠 세는 q_ 도구를 만들어 저장", "for": ["buyer"]},
                                  {"q": ""}, {"q": "max_pay_frac 0.7로 올릴 근거"}, {"q": "둘째"}, {"q": "셋째"}], ())
    assert [a["id"] for a in r["applied"]] == [1, 2]
    whys = [x["why"] for x in r["refused"]]
    assert any("비어" in w for w in whys) and any("값 지시" in w for w in whys) and any("2개까지" in w for w in whys)
    rows = questions.load(root)
    assert rows[0]["from"] == "auditor" and rows[0]["for"] == ["buyer"] and rows[1]["for"] == ["shop", "buyer"]
    assert questions._who(rows[0]) == "감사자 질문"


def test_card_pins_a_made_tool_and_shows_its_output(tmp_path, monkeypatch):
    root = _root(tmp_path)
    monkeypatch.setattr(au, "_made_names", lambda root: ["q_abc"])
    calls = []
    monkeypatch.setattr(made_tools, "call", lambda root, name, args=None, now=None, count=True: calls.append(count) or "주문 12건")
    r = au._apply_tools(root, NOW, {"card": {"buyer": ["q_abc", "q_none"], "seller": ["q_abc"]}}, list(tools.TOOLS), (), None,
                        roles=au.ROLES)
    assert r["applied"] == [{"op": "card", "role": "buyer", "name": "q_abc"}]
    assert {x["why"] for x in r["refused"]} == {"만든 도구(q_) 중에 없음", "역할은 shop 또는 buyer"}
    assert au.card_state(root) == {"shop": [], "buyer": ["q_abc"]}
    lines = au.card_lines(root, NOW, "buyer")
    assert lines == ["[감사자가 붙인 도구 q_abc] (전체는 그 도구를 부르면 나옴)\n주문 12건"] and calls == [False]
    assert au.card_lines(root, NOW, "shop") == []
    monkeypatch.setattr(made_tools, "call", lambda *a, **k: 1 / 0)
    assert au.card_lines(root, NOW, "buyer") == ["[감사자가 붙인 도구 q_abc] 만들지 못함: ZeroDivisionError"]
    r = au._apply_tools(root, NOW, {"uncard": {"buyer": ["q_abc"], "shop": ["q_abc"]}}, list(tools.TOOLS), (), None, roles=au.ROLES)
    assert len(r["applied"]) == 1 and r["refused"][0]["why"] == "붙어 있지 않음"
    assert au.card_state(root)["buyer"] == [] and au.card_lines(root, NOW, "buyer") == []


def test_card_drops_tools_that_no_longer_exist(tmp_path, monkeypatch):
    root = _root(tmp_path)
    json.dump({"card": {"shop": ["q_gone", "q_abc"]}}, open(os.path.join(root, au.TOOL_STATE), "w"))
    monkeypatch.setattr(au, "_made_names", lambda root: ["q_abc", "q_new"])
    au._apply_tools(root, NOW, {"card": {"shop": ["q_new"]}}, list(tools.TOOLS), (), None, roles=au.ROLES)
    assert au.card_state(root)["shop"] == ["q_abc", "q_new"]
