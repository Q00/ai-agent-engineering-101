#!/usr/bin/env python3
"""Render the Week 03 Contract Net architecture as a one-page vector PDF."""

from __future__ import annotations

import math
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "week03-contract-net-framework.pdf"
FONT_FILE = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
FONT = "Week03Korean"

PAGE_W = 1600
PAGE_H = 960

INK = HexColor("#19324D")
MUTED = HexColor("#5E738B")
LIGHT_INK = HexColor("#7890A8")
BG = HexColor("#F8FBFE")
WHITE = HexColor("#FFFFFF")
LINE = HexColor("#D6E2EE")
BLUE_BG = HexColor("#DFEDFC")
BLUE_EDGE = HexColor("#86AFDF")
BLUE = HexColor("#27639F")
ROSE_BG = HexColor("#FFE7EA")
ROSE_EDGE = HexColor("#DB8992")
ROSE = HexColor("#A74658")
GREEN_BG = HexColor("#E5F4EC")
GREEN_EDGE = HexColor("#8EC7A6")
GREEN = HexColor("#3F8B66")
PURPLE_BG = HexColor("#EEEAF9")
PURPLE = HexColor("#65549D")


def yy(top: float) -> float:
    return PAGE_H - top


def box(
    pdf: canvas.Canvas,
    x: float,
    top: float,
    width: float,
    height: float,
    *,
    fill=WHITE,
    stroke=LINE,
    radius: float = 15,
    line_width: float = 1.4,
) -> None:
    pdf.setFillColor(fill)
    pdf.setStrokeColor(stroke)
    pdf.setLineWidth(line_width)
    pdf.roundRect(x, PAGE_H - top - height, width, height, radius, fill=1, stroke=1)


def text(
    pdf: canvas.Canvas,
    value: str,
    x: float,
    top: float,
    *,
    size: float = 14,
    color=INK,
    max_width: float | None = None,
    align: str = "left",
) -> None:
    actual_size = size
    if max_width is not None:
        while pdfmetrics.stringWidth(value, FONT, actual_size) > max_width:
            actual_size -= 0.25
            if actual_size < 10:
                raise ValueError(f"Label cannot fit: {value!r}")
    pdf.setFont(FONT, actual_size)
    pdf.setFillColor(color)
    baseline = PAGE_H - top - actual_size
    if align == "center":
        pdf.drawCentredString(x, baseline, value)
    elif align == "right":
        pdf.drawRightString(x, baseline, value)
    else:
        pdf.drawString(x, baseline, value)


def pill(
    pdf: canvas.Canvas,
    value: str,
    x: float,
    top: float,
    width: float,
    *,
    fill=ROSE_BG,
    stroke=ROSE_EDGE,
    color=ROSE,
    height: float = 30,
    size: float = 12,
) -> None:
    box(pdf, x, top, width, height, fill=fill, stroke=stroke, radius=10, line_width=1)
    text(pdf, value, x + width / 2, top + 6, size=size, color=color, max_width=width - 16, align="center")


def arrow(
    pdf: canvas.Canvas,
    x1: float,
    top1: float,
    x2: float,
    top2: float,
    *,
    color=BLUE,
    width: float = 2.4,
    dashed: bool = False,
) -> None:
    y1, y2 = yy(top1), yy(top2)
    pdf.setStrokeColor(color)
    pdf.setFillColor(color)
    pdf.setLineWidth(width)
    pdf.setDash(6, 5) if dashed else pdf.setDash()
    pdf.line(x1, y1, x2, y2)
    pdf.setDash()
    angle = math.atan2(y2 - y1, x2 - x1)
    arm = 9
    a = angle + 0.55
    b = angle - 0.55
    path = pdf.beginPath()
    path.moveTo(x2, y2)
    path.lineTo(x2 - arm * math.cos(a), y2 - arm * math.sin(a))
    path.lineTo(x2 - arm * math.cos(b), y2 - arm * math.sin(b))
    path.close()
    pdf.drawPath(path, fill=1, stroke=0)


def circle_label(
    pdf: canvas.Canvas, letter: str, x: float, top: float
) -> None:
    cy = yy(top)
    pdf.setFillColor(ROSE_BG)
    pdf.setStrokeColor(ROSE_EDGE)
    pdf.setLineWidth(1.5)
    pdf.circle(x, cy, 22, fill=1, stroke=1)
    text(pdf, letter, x, top - 13, size=20, color=ROSE, align="center")


def protocol_row(
    pdf: canvas.Canvas,
    number: str,
    title: str,
    lines: list[str],
    top: float,
    *,
    tint=WHITE,
    accent=BLUE,
) -> None:
    x, width, height = 1108, 424, 97
    box(pdf, x, top, width, height, fill=tint, stroke=LINE, radius=12, line_width=1)
    pdf.setFillColor(accent)
    pdf.circle(x + 25, yy(top + 25), 14, fill=1, stroke=0)
    text(pdf, number, x + 25, top + 15, size=13, color=WHITE, align="center")
    text(pdf, title, x + 49, top + 12, size=14, color=INK, max_width=width - 63)
    for index, line in enumerate(lines):
        text(
            pdf,
            line,
            x + 20,
            top + 41 + index * 18,
            size=12.6,
            color=MUTED,
            max_width=width - 39,
        )


def draw() -> Path:
    if not FONT_FILE.is_file():
        raise FileNotFoundError(FONT_FILE)
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_FILE)))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(OUTPUT), pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    pdf.setTitle("Week 03 Contract Net - Full Framework")
    pdf.setAuthor("Week 03 submission 26510124")

    # Page and title.
    pdf.setFillColor(BG)
    pdf.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    text(pdf, "Week 03 Contract Net - 전체 프레임워크", 48, 34, size=27)
    text(
        pdf,
        "Week 01 도구 + Week 02 실행 루프 + Week 03 공고·입찰·낙찰",
        49,
        73,
        size=15,
        color=MUTED,
    )
    pill(pdf, "BASE  순차 입찰 · confidence_only", 1087, 36, 225, fill=BLUE_BG, stroke=BLUE_EDGE, color=BLUE, height=31)
    pill(pdf, "EXTENSION  동시 입찰 · 비용·평판", 1320, 36, 232, fill=PURPLE_BG, stroke=PURPLE, color=PURPLE, height=31)
    pdf.setStrokeColor(LINE)
    pdf.setLineWidth(1.2)
    pdf.line(48, yy(111), 1552, yy(111))

    # Input / output column.
    text(pdf, "INPUT & OUTPUT", 52, 147, size=17, color=BLUE)
    box(pdf, 48, 200, 230, 175, fill=GREEN_BG, stroke=GREEN_EDGE)
    text(pdf, "tasks.json", 67, 217, size=17, color=GREEN)
    text(pdf, "6개 고정 태스크", 67, 251, size=13.5)
    text(pdf, "계산 2 · 글쓰기 2 · 코드 2", 67, 275, size=13, max_width=190)
    text(pdf, "id · desc · gold", 67, 301, size=13)
    text(pdf, "domain · validator", 67, 325, size=13)
    text(pdf, "gold는 낙찰 후 평가에만 사용", 67, 350, size=11.5, color=MUTED, max_width=190)

    box(pdf, 48, 398, 230, 170, fill=WHITE, stroke=LINE)
    text(pdf, "make_team(condition)", 67, 415, size=15, color=BLUE, max_width=192)
    pill(pdf, "baseline", 67, 451, 103, fill=BLUE_BG, stroke=BLUE_EDGE, color=BLUE, height=27)
    pill(pdf, "homogeneous", 67, 483, 145, fill=BLUE_BG, stroke=BLUE_EDGE, color=BLUE, height=27)
    pill(pdf, "overconfident", 67, 515, 150, fill=ROSE_BG, stroke=ROSE_EDGE, color=ROSE, height=27)
    text(pdf, "C의 입찰 지시만 추가", 67, 547, size=11.5, color=MUTED)

    box(pdf, 48, 592, 230, 224, fill=WHITE, stroke=LINE)
    text(pdf, "OUTPUT", 67, 609, size=16, color=BLUE)
    text(pdf, "results.csv", 67, 648, size=14)
    text(pdf, "조건별 3회 · 총 9행", 67, 671, size=12.5, color=MUTED)
    text(pdf, "logs/*.log", 67, 705, size=14)
    text(pdf, "공고 · 입찰 · 낙찰 · 실행", 67, 728, size=12.5, color=MUTED, max_width=185)
    text(pdf, "REPORT.md", 67, 763, size=14)
    text(pdf, "설정 · 결과 · 비교 · 해석", 67, 786, size=12.5, color=MUTED, max_width=185)

    # Central blue architecture panel.
    box(pdf, 302, 135, 760, 740, fill=BLUE_BG, stroke=BLUE_EDGE, radius=25, line_width=2.0)
    text(pdf, "CONTROL + AGENTS", 324, 151, size=17, color=BLUE)
    text(
        pdf,
        "Manager/Monitor는 Python 제어 코드 · A/B/C는 LLM contractor",
        1041,
        155,
        size=12.5,
        color=MUTED,
        align="right",
    )
    arrow(pdf, 279, 278, 322, 278, color=GREEN, width=3)

    # Manager functions.
    box(pdf, 322, 203, 720, 132, fill=WHITE, stroke=BLUE_EDGE, radius=16)
    pill(pdf, "MANAGER  ·  ContractNetManager", 339, 213, 306, height=30, size=13)
    pill(pdf, "Week 03", 936, 213, 88, fill=BLUE_BG, stroke=BLUE_EDGE, color=BLUE, height=30)
    text(pdf, "run_round()  -  태스크 순회·집계", 343, 251, size=13.2, max_width=333)
    text(pdf, "_collect_sequential() / _collect_async()", 343, 276, size=12.8, max_width=333)
    text(pdf, "freeze_responses()  -  ID·마감 검증", 343, 301, size=13, max_width=333)
    text(pdf, "_record_responses() / valid_candidates()", 690, 251, size=12.8, max_width=333)
    text(pdf, "token_efficiencies() / score_candidates()", 690, 276, size=12.8, max_width=333)
    text(pdf, "choose_winner() → _award_once()", 690, 301, size=13, max_width=333)

    # Bid arrows. Separate arrowheads make the two directions explicit.
    for center in (437, 682, 927):
        arrow(pdf, center - 10, 337, center - 10, 383, color=BLUE, width=2.1)
        arrow(pdf, center + 13, 383, center + 13, 337, color=ROSE, width=2.1)
    text(pdf, "공고 ↓", 548, 350, size=11.5, color=BLUE)
    text(pdf, "입찰 ↑", 767, 350, size=11.5, color=ROSE)

    # Three actual LLM contractors, each with the same bidding functions.
    contractors = [
        (322, "A", "계산 전문"),
        (567, "B", "글쓰기 전문"),
        (812, "C", "코드 전문"),
    ]
    for x, letter, specialty in contractors:
        box(pdf, x, 389, 230, 150, fill=WHITE, stroke=ROSE_EDGE, radius=15)
        circle_label(pdf, letter, x + 38, 422)
        text(pdf, f"Contractor {letter}", x + 72, 404, size=15, max_width=143)
        text(pdf, specialty, x + 72, 430, size=12.5, color=MUTED, max_width=143)
        pdf.setStrokeColor(LINE)
        pdf.line(x + 16, yy(455), x + 214, yy(455))
        text(pdf, "request_bid()", x + 18, 467, size=13.5, color=ROSE)
        text(pdf, "parse_bid()", x + 18, 492, size=13.5, color=ROSE)
        text(pdf, "bid · confidence · reason", x + 18, 517, size=11.7, color=MUTED, max_width=194)

    # Only the winner enters a Week 02 execution loop and Week 01 tools.
    arrow(pdf, 682, 540, 682, 568, color=ROSE, width=2.5)
    text(pdf, "낙찰자만 실행", 704, 546, size=11.5, color=ROSE)
    box(pdf, 322, 574, 720, 107, fill=WHITE, stroke=BLUE_EDGE, radius=15)
    pill(pdf, "WINNER ONLY", 339, 586, 136, fill=ROSE_BG, stroke=ROSE_EDGE, color=ROSE, height=28)
    text(pdf, "execute_task() → run_react() / run_plan_execute()", 488, 588, size=14, color=INK, max_width=531)
    text(pdf, "_run_tool_calls() → ToolRuntime.execute()", 341, 624, size=13.5, color=BLUE)
    text(pdf, "Week 01 tools:", 341, 653, size=12.5, color=MUTED)
    text(
        pdf,
        "calculator · read_file · count_pattern · check_python · write_note*",
        456,
        653,
        size=12.6,
        color=GREEN,
        max_width=558,
    )
    arrow(pdf, 682, 682, 682, 710, color=BLUE, width=2.5)
    text(pdf, "결과 + 사용량", 704, 687, size=11.5, color=BLUE)

    # Deterministic monitor (separate from the winner decision).
    box(pdf, 322, 716, 720, 116, fill=WHITE, stroke=GREEN_EDGE, radius=15)
    pill(pdf, "MONITOR / EVALUATOR", 339, 728, 200, fill=GREEN_BG, stroke=GREEN_EDGE, color=GREEN, height=28)
    text(pdf, "snapshot()  →  evaluate_and_update()", 558, 731, size=13.7, max_width=462)
    text(pdf, "validate_result() · reputation_summary() · note_parse_fail()", 341, 770, size=13.5, max_width=670)
    text(pdf, "검증 후 profile 갱신 · 다음 태스크부터 반영 · 낙찰 결정에는 직접 관여하지 않음", 341, 799, size=12.2, color=MUTED, max_width=672)

    # Message protocol side column. Items 1-2 are model-facing; 3-6 are
    # code-added envelopes/events. This avoids implying a separate award API.
    text(pdf, "MESSAGE PROTOCOL", 1108, 151, size=17, color=BLUE)
    text(pdf, "실제 LLM 입출력과 내부 이벤트를 구분", 1532, 156, size=11.5, color=MUTED, align="right")
    protocol_row(
        pdf, "1", "ANNOUNCEMENT  Manager → A/B/C",
        ['User: "Task announcement: {desc}"', "로그: auction_id · request_id · task_id"],
        205, tint=WHITE, accent=BLUE,
    )
    protocol_row(
        pdf, "2", "BID  Contractor LLM → Manager",
        ['{"bid": true, "confidence": 98,', ' "reason": "짧은 이유"}  · 거절/파싱 실패는 제외'],
        312, tint=ROSE_BG, accent=ROSE,
    )
    protocol_row(
        pdf, "3", "ENVELOPE  실행 코드가 덧붙임",
        ["type: bid_response · auction_id · request_id", "contractor_id · last_task_tokens: null | int"],
        419, tint=WHITE, accent=BLUE,
    )
    protocol_row(
        pdf, "4", "AWARD  Manager 내부 결정·로그",
        ["최고 점수, 동점이면 먼저 도착한 입찰", "winner 선택 후 gold 비교 · 별도 LLM 메시지 아님"],
        526, tint=WHITE, accent=BLUE,
    )
    protocol_row(
        pdf, "5", "EXECUTION  낙찰자 ↔ 도구",
        ["Thought → tool_call → observation", "Answer → 실행 결과 · task_tokens"],
        633, tint=GREEN_BG, accent=GREEN,
    )
    protocol_row(
        pdf, "6", "UPDATE  Monitor → 다음 태스크",
        ["validation_success · profile_version", "last_task_tokens · task별 reputation"],
        740, tint=WHITE, accent=GREEN,
    )

    # Bottom annotation and legend.
    pdf.setStrokeColor(LINE)
    pdf.line(48, yy(893), 1552, yy(893))
    text(
        pdf,
        "* write_note는 기본 실험에서 거절됨. BASE는 순차 입찰·confidence_only; EXTENSION은 _collect_async + token/reputation 점수.",
        49,
        906,
        size=12.6,
        color=MUTED,
        max_width=1490,
    )
    text(
        pdf,
        "설계 출처: submissions/26510124/week-03/agents.py · contract_net.py · monitor.py · agent_tools.py",
        49,
        930,
        size=11.5,
        color=LIGHT_INK,
        max_width=1490,
    )
    pdf.showPage()
    pdf.save()
    return OUTPUT


if __name__ == "__main__":
    print(draw())
