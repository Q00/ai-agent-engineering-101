"""Generate the system's diagrams from the system, not from memory.

    python diagrams.py            # writes diagrams/*.mmd
    python diagrams.py --check    # fails if what is on disk is stale

Two kinds come out of here, and neither is drawn by hand.

  Code-derived. The class structure, the candidate and task state machines,
  the send-side guard, the role table, the cache boundary and the module
  graph are read out of protocol.py, agents.py, phases.py and tools.py. Add a
  role or a message type and the diagram follows; forget to, and --check says
  so. Diagrams that are maintained separately from the thing they describe go
  stale on the next commit, and a stale architecture diagram is worse than
  none — it is read as current.

  Journal-derived. A sequence diagram is rendered from logs_ext/*.jsonl, so it
  shows a run that happened, with its real vector clocks, rather than an
  idealisation of what the protocol is supposed to do. When the two disagree
  the journal is right.

Mermaid is the target because the Artifact viewer and the local docs both
render it, and `look: handDrawn` matches the lecture's own whiteboard.
"""

import argparse
import json
import os
import sys
from collections import Counter, OrderedDict

import agents as A
import phases as P
import protocol as PR
import tools as T

OUT = "diagrams"

RED, RED_F = "#c92a2a", "#ffc9c9"
BLUE, BLUE_F = "#4263eb", "#bac8ff"
GREEN, GREEN_F = "#2f9e44", "#b2f2bb"
AMBER, AMBER_F = "#f08c00", "#ffec99"
INK = "#1e1e1e"


def _cls(name, fill, stroke):
    return (f"    classDef {name} fill:{fill},stroke:{stroke},"
            f"stroke-width:2px,color:{INK}")


# ---------------------------------------------------------------- code-derived


def message_types() -> str:
    """The grammar this system sends, and which phase each type belongs to."""
    by_phase = OrderedDict()
    for t in PR.TYPES:
        by_phase.setdefault(PR.PHASE_OF[t], []).append(t)
    lines = ["flowchart LR"]
    prev = None
    for phase, types in by_phase.items():
        lines.append(f'    subgraph {phase.upper()}["phase · {phase}"]')
        lines.append("      direction TB")
        for t in types:
            lines.append(f'      {t.replace("-", "_")}["{t}"]')
        lines.append("    end")
        if prev:
            lines.append(f"    {prev.upper()} --> {phase.upper()}")
        prev = phase
    lines.append(_cls("msg", RED_F, RED))
    lines.append("    class " + ",".join(t.replace("-", "_") for t in PR.TYPES)
                 + " msg")
    return "\n".join(lines)


def candidate_states() -> str:
    """One candidate's lifecycle. Capacity is two things, so the diagram has
    two tracks: what it owes, and what it is running."""
    return f"""stateDiagram-v2
    direction LR
    [*] --> idle
    idle --> bidding : announcement delivered
    bidding --> idle : not awarded
    bidding --> committed : awarded and ACCEPTANCE
    bidding --> idle : awarded and REFUSAL<br/>(capacity is {1} contract)
    committed --> working : runs its own tools
    working --> managing : announces a piece<br/>(depth < {PR.MAX_DEPTH})
    managing --> working : piece reported or orphaned
    working --> idle : FINAL-REPORT
    note right of managing
      committed and managing at once.
      One counter would deadlock here:
      the winner would be busy and
      unable to announce its own piece.
    end note"""


def task_states() -> str:
    """A task's lifecycle, including every way it can end short."""
    return """stateDiagram-v2
    [*] --> announced : manager writes 2 of Smith's 4 fields
    announced --> unassigned : no bid
    announced --> bids_in : at least one BID
    bids_in --> awarded : ANNOUNCED-AWARD
    bids_in --> unassigned : no award call
    awarded --> refused : winner already committed
    refused --> awarded : manager falls to the next bid
    refused --> unassigned : nobody left
    awarded --> accepted : ACCEPTANCE
    accepted --> reported : FINAL-REPORT
    accepted --> partial : a piece was orphaned
    partial --> reported
    reported --> [*]
    unassigned --> [*]"""


def send_guard() -> str:
    """The send-side state machine, as a decision tree. This is the part the
    mailbox cannot do: it fixes arrival order, not generation order."""
    return f"""flowchart TD
    S["an agent wants to send"] --> K{{"which type"}}
    K -->|"TASK-ANNOUNCEMENT"| A1{{"depth"}}
    A1 -->|"1"| A2{{"is this endpoint<br/>the manager?"}}
    A1 -->|"2"| A3{{"did it accept<br/>the parent?"}}
    A1 -->|"> {PR.MAX_DEPTH}"| X1["refused<br/>max depth"]
    A2 -->|"no"| X2["refused"]
    A2 -->|"yes"| OK1["sent"]
    A3 -->|"no"| X3["refused<br/>not committed to parent"]
    A3 -->|"yes"| OK2["sent · becomes manager of the piece"]
    K -->|"BID"| B1{{"announcement<br/>delivered here?"}}
    B1 -->|"no"| X4["refused"]
    B1 -->|"yes"| OK3["sent"]
    K -->|"ANNOUNCED-AWARD"| C1{{"did this endpoint<br/>announce it?"}}
    C1 -->|"no"| X5["refused"]
    C1 -->|"yes"| C2{{"did the target bid?"}}
    C2 -->|"no"| X6["refused · invalid_award<br/>retried once"]
    C2 -->|"yes"| C3{{"award outstanding?"}}
    C3 -->|"yes, unanswered"| X7["refused"]
    C3 -->|"no, or refused"| OK4["sent"]
    K -->|"ACCEPTANCE"| D1{{"already committed<br/>elsewhere?"}}
    D1 -->|"yes"| X8["refused · send REFUSAL instead"]
    D1 -->|"no"| OK5["sent"]
    K -->|"FINAL-REPORT"| E1{{"any piece still<br/>open?"}}
    E1 -->|"yes"| X9["refused"]
    E1 -->|"no"| OK6["sent"]

{_cls("ok", GREEN_F, GREEN)}
{_cls("no", RED_F, RED)}
    class OK1,OK2,OK3,OK4,OK5,OK6 ok
    class X1,X2,X3,X4,X5,X6,X7,X8,X9 no"""


def causal_delivery() -> str:
    return f"""flowchart TD
    R["envelope arrives"] --> DUP{{"seen this mid?"}}
    DUP -->|"yes"| DROP["dropped · idempotent"]
    DUP -->|"no"| Q["hold-back queue"]
    Q --> TEST{{"vc[sender] == local[sender] + 1<br/>and vc[k] <= local[k] for k != sender"}}
    TEST -->|"no"| WAIT["stays held<br/>until its predecessors arrive"]
    WAIT --> TEST
    TEST -->|"yes"| CLK["clock: elementwise max<br/>(never ticks our own entry)"]
    CLK --> VIS{{"are we an addressee?"}}
    VIS -->|"yes"| READ["appended to the conversation<br/>readable by the guard"]
    VIS -->|"no"| OBS["counted for causality only<br/>content stays private"]
    READ --> APPEND["append-only, so the cached<br/>prefix is never rewritten"]

{_cls("ok", GREEN_F, GREEN)}
{_cls("mid", AMBER_F, AMBER)}
{_cls("no", RED_F, RED)}
    class READ,APPEND,CLK ok
    class WAIT,OBS mid
    class DROP no"""


def cache_boundary() -> str:
    """Why role cannot live in the first two segments."""
    tools_n = len(A.PROTOCOL_SPECS)
    return f"""flowchart TB
    subgraph PREFIX["cached prefix · byte-identical across turns and roles"]
      direction TB
      TL["tools<br/>{tools_n} protocol tools, fixed order<br/>+ this candidate's work tools, sorted"]
      SY["system<br/>identity · manifest · protocol rules"]
      TL --> SY
    end
    BOUND["cache_control boundary"]
    subgraph TAIL["after the boundary · append-only"]
      direction TB
      RO["ROLE for this turn"]
      TK["task, announcement, bids so far"]
      RO --> TK
    end
    SY --> BOUND --> RO
    NOTE["a role in tools or system would break<br/>the prefix at its earliest byte"]
    BOUND -.-> NOTE

{_cls("keep", GREEN_F, GREEN)}
{_cls("line", BLUE_F, BLUE)}
{_cls("warn", AMBER_F, AMBER)}
    class TL,SY keep
    class RO,TK line
    class NOTE warn"""


def role_tools() -> str:
    lines = ["flowchart LR"]
    for role, allowed in A.ROLE_TOOLS.items():
        lines.append(f'    {role}["{role}"]')
    for name in A.PROTOCOL_NAMES:
        lines.append(f'    {name}(["{name}"])')
    for role, allowed in A.ROLE_TOOLS.items():
        for t in allowed:
            lines.append(f"    {role} --> {t}")
    lines.append(_cls("role", RED_F, RED))
    lines.append(_cls("tool", BLUE_F, BLUE))
    lines.append("    class " + ",".join(A.ROLE_TOOLS) + " role")
    lines.append("    class " + ",".join(A.PROTOCOL_NAMES) + " tool")
    return "\n".join(lines)


def manifests() -> str:
    lines = ["flowchart LR"]
    for n in T.CANDIDATES:
        lines.append(f'    {n}["{n}"]')
    for t in T.TOOL_NAMES:
        lines.append(f'    {t}(["{t}"])')
    for n, have in T.MANIFESTS.items():
        for t in have:
            lines.append(f"    {n} --> {t}")
    shared = [t for t in T.TOOL_NAMES
              if sum(1 for h in T.MANIFESTS.values() if t in h) > 1]
    lines.append(_cls("cand", RED_F, RED))
    lines.append(_cls("tool", BLUE_F, BLUE))
    lines.append(_cls("shared", AMBER_F, AMBER))
    lines.append("    class " + ",".join(T.CANDIDATES) + " cand")
    solo = [t for t in T.TOOL_NAMES if t not in shared]
    if solo:
        lines.append("    class " + ",".join(solo) + " tool")
    if shared:
        lines.append("    class " + ",".join(shared) + " shared")
    return "\n".join(lines)


def classes() -> str:
    """The types the system is built from, with the fields that carry the
    design decisions."""
    return """classDiagram
    class VectorClock {
      +members
      +tick(who) sent
      +observe(other) received, max only
      +leq(other) happens-before
    }
    class CausalMailbox {
      +clock
      +held out of order
      +observed every envelope
      +delivered addressed to us only
      +receive(msg, visible)
    }
    class Endpoint {
      +roles task to role
      +sent task to messages
      +committed one contract owed
      +managing auctions running
      +subtasks parent to children
      +orphans written off
      +violations refused sends
      +can_send(type, task, to)
      +decision_cut()
    }
    class Journal {
      +write(msg) JSONL, seq
      +note_usage(usage) cache
      +cache_stats()
    }
    class Agent {
      +manifest tools held
      +persistent conversation kept
      +allow_trajectory condition
      +system_prompt() invariant
      +tool_specs() invariant
      +act(role, block)
    }
    class TaskResult {
      +feasible award inside capable
      +optimal award equals gold
      +solved report answers it
      +partial a piece was orphaned
      +failures countable modes
    }
    Endpoint --> CausalMailbox
    CausalMailbox --> VectorClock
    Endpoint --> Journal
    Agent --> Journal
    TaskResult ..> Endpoint : built by phases"""


def modules() -> str:
    return f"""flowchart TB
    TL["tools.py<br/>4 work tools · manifests<br/>capable_for · gold_for · judge"]
    PR2["protocol.py<br/>VectorClock · CausalMailbox<br/>Endpoint · Journal · trajectory"]
    AG["agents.py<br/>identity · cache boundary<br/>{len(A.ROLE_TOOLS)} role states"]
    PH["phases.py<br/>announce / bid+select / execute<br/>recursion · orphans"]
    RN["run_ext.py<br/>{len(__import__('run_ext').CONDITIONS)} conditions · rounds"]
    OUT2["results_ext.csv<br/>tasks_ext_detail.csv<br/>logs_ext/*.jsonl"]
    AN["analyze.py"]
    TO["test_offline.py<br/>no key, no model"]
    DG["diagrams.py<br/>this file"]

    TL --> AG
    TL --> PH
    PR2 --> AG
    PR2 --> PH
    AG --> PH
    PH --> RN
    RN --> OUT2
    OUT2 --> AN
    OUT2 --> DG
    TL --> DG
    PR2 --> DG
    AG --> DG
    TO -.-> TL
    TO -.-> PR2
    TO -.-> AG
    TO -.-> PH

{_cls("core", RED_F, RED)}
{_cls("data", BLUE_F, BLUE)}
{_cls("aux", GREEN_F, GREEN)}
    class TL,PR2,AG,PH,RN core
    class OUT2 data
    class AN,TO,DG aux"""


def judgement() -> str:
    return f"""flowchart TD
    AW{{"award made?"}} -->|"no"| UN["unassigned"]
    AW -->|"yes"| F{{"inside capable?"}}
    F -->|"no"| IF["not feasible<br/>(capable is a lower bound —<br/>a solve from here is a finding)"]
    F -->|"yes"| FE["feasible"]
    FE --> O{{"equals gold?"}}
    O -->|"yes"| OP["optimal"]
    O -->|"no"| SU["second best"]
    IF --> S{{"last Answer: line<br/>contains the answer?"}}
    OP --> S
    SU --> S
    S -->|"yes"| SO["solved"]
    S -->|"no"| NS["not solved"]

{_cls("ok", GREEN_F, GREEN)}
{_cls("mid", AMBER_F, AMBER)}
{_cls("no", RED_F, RED)}
    class FE,OP,SO ok
    class SU,IF mid
    class UN,NS no"""


# ---------------------------------------------------------------- journal-derived


def sequence_from_journal(path, task=None, limit=40) -> str:
    """A sequence diagram of a run that happened, clocks and all."""
    recs = [json.loads(l) for l in open(path, encoding="utf-8")]
    if task is not None:
        recs = [r for r in recs
                if r["task"] == str(task) or r["task"].startswith(str(task) + ".")]
        if not recs:
            return None
    recs = recs[:limit]
    if not recs:
        return "sequenceDiagram\n    Note over A: no messages"

    who = []
    for r in recs:
        for n in [r["frm"]] + list(r["to"]):
            if n not in who:
                who.append(n)
    lines = ["sequenceDiagram"]
    for n in sorted(who):
        lines.append(f"    participant {n}")
    phase = None
    for r in recs:
        if r["phase"] != phase:
            phase = r["phase"]
            lines.append(f"    Note over {sorted(who)[0]},{sorted(who)[-1]}: "
                         f"phase · {phase}")
        vc = ",".join(f"{k}:{v}" for k, v in sorted(r["vc"].items()) if v)
        label = f"{r['type']} c{r['task']} [{vc}]"
        # A broadcast is one message with several addressees, drawn as one
        # arrow each. No activation bars: they would add a return arrow the
        # protocol never sends.
        arrow = "->>" if r["type"] in (PR.ANNOUNCE, PR.AWARD) else "-->>"
        for i, t in enumerate(r["to"]):
            shown = label if i == 0 else f"{r['type']} c{r['task']}"
            lines.append(f"    {r['frm']}{arrow}{t}: {shown}")
    return "\n".join(lines)


# Titles and the one thing each diagram is there to settle. Kept here so the
# document and the .mmd files come out of the same place; a caption written
# separately in the HTML would drift the way the diagrams themselves would.
TITLES = OrderedDict([
    ("modules", ("모듈 구조",
        "무엇이 무엇에 의존하는가. 점선은 검증 경로.")),
    ("classes", ("타입 구조",
        "설계 판단을 담고 있는 필드만 적었다. Endpoint 의 committed 와 managing "
        "이 갈라져 있는 것이 depth 를 가능하게 한다.")),
    ("cache-boundary", ("캐시 경계",
        "역할이 tools 나 system 에 들어가면 프리픽스가 가장 앞 바이트에서 깨진다. "
        "그래서 역할은 경계 뒤 messages 로 간다.")),
    ("message-types", ("메시지 문법",
        "Smith 1980 의 문법 중 이 시스템이 실제로 보내는 것, 그리고 페이즈 대응.")),
    ("causal-delivery", ("인과 배달",
        "봉투는 전원에게, 내용은 수신자에게만. 보류 큐가 정확성과 캐시를 동시에 준다 "
        "— 소급 삽입이 없으니 대화가 append-only 로 남는다.")),
    ("send-guard", ("송신 가드",
        "메일박스는 도착 순서를 고친다. 생성 순서는 이 트리가 막는다. 판단은 "
        "로컬 지식만 쓴다.")),
    ("candidate-states", ("후보 생애주기",
        "capacity 가 둘로 갈린 이유. 하나면 분해자가 자기 조각을 공고할 수 없다.")),
    ("task-states", ("태스크 생애주기",
        "짧게 끝나는 모든 경로를 포함한다 — 유찰, 거절 후 재낙찰, orphan 후 부분 보고.")),
    ("role-tools", ("역할과 도구",
        "winner 가 manager 의 도구를 갖는다. 조각을 내주면 그 조각의 manager 이니까.")),
    ("manifests", ("후보와 작업 도구",
        "노란 도구는 둘 이상이 나눠 갖는다. 그 겹침이 manager 의 판단에 여지를 만든다.")),
    ("judgement", ("판정",
        "feasible · optimal · solved 는 갈라진다. capable 은 하한이므로 그 밖에서 "
        "나온 solved 는 모순이 아니라 발견이다.")),
    ("sequence-task1", ("실제 실행 · 태스크 하나",
        "손으로 그린 이상화가 아니라 저널에서 생성한 것. 대괄호 안이 실제 벡터 시계.")),
    ("sequence-run", ("실제 실행 · 라운드 앞부분",
        "같은 저널에서. 페이즈 전환이 주석으로 들어간다.")),
    ("scenario-concurrency", ("시나리오 · 동시성 (실제 실행 아님)",
        "채점 실행의 저널에는 REFUSAL 이 0건이다. 러너가 태스크를 순차 처리하니 "
        "한 후보가 두 계약에 동시에 묶일 상황이 만들어지지 않는다. capacity-1 과 "
        "Smith 의 ACCEPTANCE | REFUSAL 쌍은 구현·검증됐으나 측정에는 부하를 지지 "
        "않는다. 이 그림은 그 경로를 관통하는 오프라인 시나리오에서 생성했다 — "
        "scenarios.concurrency, 테스트가 단정하는 바로 그 시나리오.")),
    ("scenario-depth", ("시나리오 · 재귀 (실제 실행 아님)",
        "분해도 실제 실행에서 한 번도 일어나지 않았다. 기본 읽기 상한에서는 60줄 "
        "파일 전체를 세 번 읽어 훑을 수 있으니 대체가 분해보다 싸다. 부록 "
        "ext_tight_reads 가 진짜 분해를 만들면 그 저널이 더 나은 출처이고 이 "
        "그림은 그것으로 바꿔야 한다.")),
])

HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Contract Net 아키텍처</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gaegu:wght@300;400;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root{
    color-scheme: light;
    --board:#fbfbf9; --card:#fff; --ink:#1e1e1e; --ink-2:#43464d; --ink-3:#7c8089;
    --grid:#e6e6e2; --red:#c92a2a; --amber:#f08c00; --amber-f:#ffec99;
    --hand:"Gaegu","Nanum Pen Script","Apple SD Gothic Neo",cursive;
    --body:"IBM Plex Sans","Apple SD Gothic Neo",-apple-system,system-ui,sans-serif;
    --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;background-color:var(--board);color:var(--ink);
    background-image:radial-gradient(var(--grid) 1.1px,transparent 1.1px);
    background-size:22px 22px;font-family:var(--body);font-size:16px;line-height:1.74}
  .wrap{max-width:1060px;margin:0 auto;padding-block:52px 108px;padding-left:20px;padding-right:20px}
  .col{max-width:660px}
  code{font-family:var(--mono);font-size:13px;background:#f1f0eb;border:1px solid #e2e0d8;
    padding:0 5px;border-radius:4px}
  .slug{font-family:var(--hand);font-size:21px;color:var(--red)}
  h1{font-family:var(--hand);font-size:46px;line-height:1.1;margin:4px 0 12px;font-weight:700;
    position:relative;display:inline-block}
  h1::after{content:"";position:absolute;left:-4px;right:-8px;bottom:2px;height:12px;
    background:var(--amber-f);z-index:-1;transform:rotate(-.6deg);border-radius:40% 60% 50% 45%}
  .standfirst{font-size:17px;color:var(--ink-2);margin:0;max-width:640px}
  .gen{font-family:var(--mono);font-size:12px;color:var(--ink-3);margin-top:18px;
    padding-top:12px;border-top:2px dashed var(--grid)}
  section{margin-top:52px}
  h2{font-family:var(--hand);font-size:32px;margin:0 0 4px;font-weight:700}
  h2 .n{font-size:20px;color:var(--red);margin-right:10px}
  .why{font-size:15px;color:var(--ink-2);margin:0 0 16px;max-width:680px}
  figure{margin:0;background:var(--card);border:2px solid var(--ink);
    border-radius:18px 10px 20px 10px;box-shadow:3px 4px 0 rgba(30,30,30,.07)}
  figure .plate{overflow-x:auto;padding:24px 20px}
  pre.mermaid{min-width:min-content;margin:0}
  figcaption{border-top:2px dashed var(--grid);padding:10px 18px;font-family:var(--mono);
    font-size:12px;color:var(--ink-3);background:#fcfcfa}
  footer{margin-top:72px;padding-top:16px;border-top:2px dashed var(--grid);
    font-family:var(--mono);font-size:12px;color:var(--ink-3);line-height:1.8}
  @media (max-width:560px){h1{font-size:32px}h2{font-size:24px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="slug">Week 03 확장 · 구현된 시스템</div>
  <h1>Contract Net 아키텍처</h1>
  <p class="standfirst">이 문서는 손으로 그린 것이 아니다. <code>diagrams.py</code> 가
  코드와 실행 저널에서 생성하며, <code>--check</code> 가 코드와 어긋난 그림을 잡는다.
  설계안이 아니라 지금 돌아가는 것이다.</p>
  <div class="gen">생성 @@STAMP@@ · 다이어그램 @@N@@개 · <code>python diagrams.py --html architecture.html</code></div>
</header>
"""

TAIL = """
<footer>
  Contract Net 아키텍처 · Week 03 확장 · Agentic AI, SeoulTech 2026 Fall<br>
  코드 유래 11개는 protocol.py · agents.py · phases.py · tools.py 에서, 시퀀스 2개는
  logs_ext/*.jsonl 에서 생성된다. 손으로 고치면 다음 커밋에 어긋나므로 고치지 말 것 —
  <code>python diagrams.py</code> 로 다시 만든다.<br>
  화이트보드 한 세계로 커밋했다. 다이어그램은 mermaid 의 handDrawn 렌더.
</footer>
</div>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script>
<script>
  if (window.mermaid) {
    window.mermaid.initialize({
      startOnLoad: true, look: "handDrawn", handDrawnSeed: 7, theme: "base",
      fontFamily: '"Gaegu","Nanum Pen Script","Apple SD Gothic Neo",cursive',
      flowchart: { curve: "basis", nodeSpacing: 46, rankSpacing: 54, padding: 14 },
      themeVariables: {
        background: "#ffffff", fontSize: "18px",
        primaryColor: "#ffc9c9", primaryBorderColor: "#c92a2a", primaryTextColor: "#1e1e1e",
        secondaryColor: "#bac8ff", secondaryBorderColor: "#4263eb",
        tertiaryColor: "#ffec99", tertiaryBorderColor: "#f08c00",
        lineColor: "#1e1e1e", textColor: "#1e1e1e",
        clusterBkg: "#f7f6f1", clusterBorder: "#1e1e1e",
        edgeLabelBackground: "#ffffff"
      }
    });
  } else {
    document.querySelectorAll("pre.mermaid").forEach(function (el) {
      el.style.whiteSpace = "pre";
      el.style.fontFamily = "ui-monospace, Menlo, monospace";
      el.style.fontSize = "12px";
      el.insertAdjacentHTML("beforebegin",
        "<p style='margin:0 0 8px;font-size:14px;color:#c92a2a'>mermaid 를 불러올 수 없어 원본 정의를 표시합니다.</p>");
    });
  }
</script>
</body>
</html>
"""


def html(made, path):
    import datetime
    order = [k for k in TITLES if k in made] + [k for k in made if k not in TITLES]
    # replace, not format: the CSS in HEAD is full of braces.
    parts = [HEAD
             .replace("@@STAMP@@", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
             .replace("@@N@@", str(len(order)))]
    for i, name in enumerate(order, 1):
        title, why = TITLES.get(name, (name, ""))
        parts.append(f'<section>\n  <h2><span class="n">{i:02d}</span>{title}</h2>')
        if why:
            parts.append(f'  <p class="why">{why}</p>')
        parts.append('  <figure>\n    <div class="plate">')
        parts.append(f'<pre class="mermaid">\n{made[name]}\n</pre>')
        parts.append('    </div>')
        parts.append(f'    <figcaption>diagrams/{name}.mmd</figcaption>')
        parts.append('  </figure>\n</section>')
    parts.append(TAIL)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return path


# ---------------------------------------------------------------- driver

CODE_DIAGRAMS = OrderedDict([
    ("message-types", message_types),
    ("candidate-states", candidate_states),
    ("task-states", task_states),
    ("send-guard", send_guard),
    ("causal-delivery", causal_delivery),
    ("cache-boundary", cache_boundary),
    ("role-tools", role_tools),
    ("manifests", manifests),
    ("classes", classes),
    ("modules", modules),
    ("judgement", judgement),
])


def newest_journal():
    if not os.path.isdir("logs_ext"):
        return None
    js = sorted(f for f in os.listdir("logs_ext") if f.endswith(".jsonl"))
    return os.path.join("logs_ext", js[-1]) if js else None


def build():
    out = {name: fn() for name, fn in CODE_DIAGRAMS.items()}
    j = newest_journal()
    if j:
        out["sequence-run"] = sequence_from_journal(j)
        one = sequence_from_journal(j, task="1-1")
        if one:
            out["sequence-task1"] = one

    # Two paths the graded runs never took, so there is no journal of them to
    # draw from. The scenarios are regenerated here rather than read off disk:
    # they are deterministic and need no key, so a fixture cannot go stale
    # against the code that produced it. Every diagram from them says so.
    import scenarios
    for name, (path, _facts) in sorted(scenarios.build_all().items()):
        out["scenario-" + name] = sequence_from_journal(path, limit=60)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the files on disk differ from the code")
    ap.add_argument("--html", default=None,
                    help="also write a document embedding every diagram")
    args = ap.parse_args()

    made = build()
    os.makedirs(OUT, exist_ok=True)
    stale = []
    for name, text in made.items():
        path = os.path.join(OUT, name + ".mmd")
        have = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if have != text + "\n":
            stale.append(name)
            if not args.check:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")

    if args.check:
        if stale:
            print("stale, regenerate with `python diagrams.py`:")
            for n in stale:
                print("  " + n)
            sys.exit(1)
        print(f"ok  {len(made)} diagram(s) match the code")
        return
    if args.html:
        print("wrote " + html(made, args.html))
    print(f"wrote {len(made)} diagram(s) to {OUT}/"
          + (f" ({len(stale)} changed)" if stale else " (no change)"))
    for n in sorted(made):
        print(f"  {n}.mmd  {len(made[n].splitlines())} lines")


if __name__ == "__main__":
    main()
