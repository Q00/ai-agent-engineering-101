"""The auditor's tool lifecycle block (2026-11-17): made tools with maker, uses and idle hours, and analyze code run
again and again as a q_ tool candidate."""

import json

from kernel import auditor

NOW = 1_794_900_000.0


def _tool(name, by, **kw):
    return {"name": name, "description": "최근 기록의 줄 수를 센다", "code": "def run(load, args):\n    return 1", "kind": "code",
            "role": by, "by": by, **kw}


def _setup(tmp_path, tools, analyze):
    (tmp_path / "a2").mkdir()
    (tmp_path / "a2" / "tools_made.json").write_text(json.dumps(tools), encoding="utf-8")
    with open(tmp_path / "a2" / "trajectory.jsonl", "w", encoding="utf-8") as f:
        for ts, code in analyze:
            f.write(json.dumps({"ts": ts, "agent": "operator", "kind": "tool", "role": "shop", "name": "analyze",
                                "args": json.dumps({"code": code})}) + "\n")
        f.write("not json\n")
    return str(tmp_path)


def test_lifecycle_marks_idle_tools_and_repeated_code(tmp_path):
    code = "def run(load, args):\n    rows = load('events')\n    return len(rows)"
    root = _setup(tmp_path, [
        _tool("q_fresh", "analyst", created_ts=NOW - 50 * 3600, uses=9, last_used_ts=NOW - 3600),
        _tool("q_old", "analyst", created_ts=NOW - 100 * 3600, uses=2, last_used_ts=NOW - 80 * 3600),
        _tool("q_never", "operator", created_ts=NOW - 10 * 3600, uses=0),
    ], [(NOW - 100, code), (NOW - 200, code + "  "), (NOW - 300, code), (NOW - 400, "def run(load, args):\n    return 1"),
        (NOW - 30 * 3600, code)])
    text = auditor._lifecycle_block(root, NOW)
    assert "q_fresh | analyst | 50시간 전 만듦 | 9번 | 마지막 사용 1시간 전\n" in text + "\n"
    assert "q_old | analyst | 100시간 전 만듦 | 2번 | 마지막 사용 80시간 전 (48시간 넘게 안 씀)" in text
    assert "q_never | operator | 10시간 전 만듦 | 0번 | 쓴 적 없음 (48시간 넘게 안 씀)" in text
    assert "같은 analyze 코드 3번 실행 (shop): rows = load('events')" in text
    assert text.count("같은 analyze") == 1


def test_lifecycle_empty_and_in_prompt(tmp_path):
    root = _setup(tmp_path, [], [])
    assert auditor._lifecycle_block(root, NOW) == "없음"
    assert "## 만든 도구의 수명과 반복 분석" in auditor.build_prompt(root, NOW)
