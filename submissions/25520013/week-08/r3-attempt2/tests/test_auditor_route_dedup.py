"""Routed auditor findings: the same text is not written twice, and an operator prompt keeps one auditor slot so the
investigator's answer to the operator's own question stays visible (verifier 11-12: four hourly audits filled all
three PROMPT_N slots with the same [shop] line)."""

from kernel import auditor, questions

NOW = 1_794_000_000.0


def _answer(root, ts):
    questions.add_finding(
        root,
        {"id": 7, "from": "shop", "q": "왜 안 팔림"},
        "가격이 높음",
        [],
        "",
        ["shop"],
        ts,
    )


def test_same_auditor_finding_is_routed_once(tmp_path):
    root = str(tmp_path)
    for h in range(4):
        auditor._route_findings(
            root, NOW + h * 3600, ["[shop] 반복 호출 3건"], ["shop", "buyer"]
        )
    rows = [r for r in questions.findings(root) if r.get("from") == "auditor"]
    assert len(rows) == 1 and rows[0]["for"] == ["shop"]


def test_prompt_keeps_one_auditor_slot(tmp_path):
    root = str(tmp_path)
    _answer(root, NOW)
    for h, text in enumerate(["[shop] a", "[shop] b", "[shop] c"]):
        auditor._route_findings(root, NOW + (h + 1) * 600, [text], ["shop"])
    page = "\n".join(questions.prompt_lines(root, "shop", NOW + 3600))
    assert "가격이 높음" in page
    assert "[shop] c" in page and "[shop] a" not in page and "[shop] b" not in page


def test_route_ignores_non_strings(tmp_path):
    auditor._route_findings(str(tmp_path), NOW, [None, 3, "[buyer] x"], ["buyer"])
    assert [r["finding"] for r in questions.findings(str(tmp_path))] == ["[buyer] x"]
