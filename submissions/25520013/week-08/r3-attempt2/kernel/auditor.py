"""Auditor: the meta-harness over the operator model (human code). It checks how the operator works, not what it decides.

One model call per run reads the audit counts (kernel.audit), the tool table, the experiment ledger, the last compact
wakes, the operator's notes and the guidance history, and may answer with a new working-method guidance for the
operator and tool list changes (hide, show, describe, remove operator-made q_ tools). It never sets policy values: a
guidance that attaches a number to a policy key is refused. Each guidance version is judged by code
(judge_guidance): mistakes per wake before and after, reverted to the previous text when clearly worse.
Missing sibling modules (kernel.audit, kernel.made_tools) count as no data. Corrupt state files read as empty.

With roles (the two role operators, shop and buyer, kernel.operator --role) one run looks at both: guidance, its
versions and their judging are per role (operator_guidance_<role>.md, that role's wakes), the prompt gets a per-role
tool table, and the answer may also move tools into or out of a role's recommended list (recommend, unrecommend).
"""

import json
import math
import os
import re
import tempfile
from collections import Counter

from kernel import agent_registry as reg
from kernel import common as c
from kernel import experiments as ex
from kernel import llm
from kernel.agent import ParseError, parse_last_object
from kernel.llm import ModelError, ModelRefused

try:
    from kernel import audit
except ImportError:  # sibling not there yet: no audit data
    audit = None
try:
    from kernel import made_tools
except ImportError:  # sibling not there yet: no operator-made tools
    made_tools = None
try:
    from kernel import code_edit
except ImportError:  # sibling not there yet: no code changes
    code_edit = None

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB = os.path.join(HERE, "AUDITOR_JOB.md")
GUIDANCE = "a2/operator_guidance.md"
GUIDANCE_CHARS = 1500
TOOL_STATE = "a2/tool_state.json"  # {"hidden": [names], "desc": {name: text}}
STATE = "a2/auditor_state.json"  # last_run_ts, verdicts_seen, guidance history
NOTES = "a2/operator_notes.md"
TRAJ = "a2/trajectory.jsonl"
CORE_TOOLS = ("policy", "brief_section", "guide", "tool_search")  # never hidden, never unrecommended
ROLES = ("shop", "buyer")
ROLE_KO = {"shop": "가게 운영자(shop)", "buyer": "구매 운영자(buyer)"}
HARNESS_TOOLS = (
    "versions",
    "harness_read",
    "replay_probe",
)  # operator.HARNESS_TOOLS: not listed in compact mode
# 11-11 성수님: 감사는 길어도 1시간마다. 6시간 주기로는 분리 뒤 첫 1시간에 한 번도 돌지 않았고 운영자의 반복 실수
# (거절된 수정을 세 번 다시 냄)를 사람이 먼저 봄
INTERVAL_S = 3600  # env A2_AUDITOR_INTERVAL_S overrides
MIN_GAP_S = 1800
VERDICTS_TRIGGER = 3
MIN_WAKES = 4  # wakes needed under a guidance before it is judged
WORSE_MISTAKES_RATIO = (
    1.25  # revert when after.mistakes_per_wake > before * ratio + slack
)
WORSE_MISTAKES_SLACK = 0.5
HISTORY_MAX = 12
HISTORY_SHOWN = 4
DESC_CHARS = 160
TOOL_CHANGES_MAX = 4
PROMPT_CHARS = 28_000  # 11-12: 24,000 held the blocks at about 23,000; the board block adds about 1,200, the
# values switched outside the kernel about 600 and the newest refused code edits about 800
NOTES_SHOWN = 800
WAKES_SHOWN = 3
RUNS_MAX = 12  # the auditor's own memory: its past runs kept in the state file
RUNS_SHOWN = 4
RUNS_CHARS = 1_800
WAKE_NOTE_CHARS = 300
WAKE_LOOKBACK_S = 72 * 3600
FINDINGS_MAX = 5
ASKS_MAX = 2  # silent backstop: questions the auditor may file per run
CARD_MAX = 3  # silent backstop: tools pinned to one role's state card
CARD_CHARS = 600  # per pinned tool on the card
GAPS_SHOWN = 10
GAP_RX = re.compile(r"확인하지 못|확인할 수 없|확인되지 않|확정할 수 없|셀 수 없|이을 수 없|연결할 수 없|남아 있지 않|기록이 없|알 수 없|구분할 수 없|나눌 수 없")
AGENT_HISTORY_MAX = 24  # the auditor's agent changes kept in its state file
AGENT_CHANGES_MAX = 3  # silent backstop: agent changes applied per run
CONTEXT_SECTIONS = 6  # largest prompt sections shown per agent in the prompt-size block
POLICY_KEYS = (
    "price_mult",
    "min_margin",
    "concession",
    "max_rounds",
    "stock_target",
    "qty_per_buy",
    "warranty_uplift",
    "fresh_discount",
    "warranty_hours",
    "grade_floor_list",
    "c_first_leaves",
    "max_pay_frac",
    "open_frac",
    "step_frac",
    "accept_c",
)
# A key with a number attached (only a particle, an operator or a bracket in between) is a value instruction.
# "price_mult 실험을 3건" is not: the number belongs to another word.
VALUE_RX = re.compile(
    r"(?<![A-Za-z_])(?:" + "|".join(POLICY_KEYS) + r")(?:\.[A-Za-z](?![A-Za-z_]))?[`'\"\s)\]]*(?:값)?"
    r"(?:\s*(?:를|을|은|는|이|가|의|=|:|→|->|to))*\s*[(\[]?\s*\.?\d",
    re.IGNORECASE,
)
AGENTS_RULE = ("agents는 에이전트를 만들거나(create, 읽기 권한만), 일 문서와 깨는 때를 고치거나(update, 운영자, 조사원, 너 중 누가 "
               "만든 것이든), 은퇴시킴(retire). 커널이 job 앞에 머리글(만든 쪽, 권한, 넘을 수 없는 선)을 붙임. for는 결과를 읽을 "
               "운영자(생략하면 둘 다). 사람이 쓴 일 문서(운영자, 조사원, 감사자)는 에이전트가 아니라 고치지 못함. 네가 만들거나 고친 "
               "에이전트는 판마다 기록되고, 그 에이전트의 점검당 실수가 전보다 나빠지면 커널이 이전 일 문서로 되돌림. policy 키 "
               "이름 뒤에 숫자를 붙인 일 문서는 거절됨")
SCHEMA = """JSON 객체 하나로만 답함. 모든 항목은 선택임:
{"findings": ["<실수와 근거 숫자>", ... 5개까지],
 "guidance": "<운영자에게 줄 새 안내 전체, 1,500자 안. 바꿀 것이 없으면 null>",
 "tools": {"hide": ["<도구 이름>"], "show": ["<도구 이름>"], "describe": {"<도구 이름>": "<새 설명 160자 안>"},
           "remove": ["q_<운영자가 만든 도구>"]},
 "agents": [{"op": "create|update|retire", "name": "<영소문자_숫자>", "job": "<에이전트의 일 문서 전체>", "wake": {"every_min": <분>, "on_ask": true}, "for": ["shop", "buyer"], "reason": "<근거 숫자>"}],
 "note": "<이번 감사 한 줄 요약>"}
도구 변경은 한 번에 4개까지. policy 키 이름 뒤에 숫자를 붙이면 안내 전체가 거절됨.
""" + AGENTS_RULE
SCHEMA_ROLES = """JSON 객체 하나로만 답함. 모든 항목은 선택임:
{"findings": ["<[shop] 또는 [buyer]와 실수, 근거 숫자>", ... 5개까지],
 "guidance": {"shop": "<가게 운영자에게 줄 새 안내 전체, 1,500자 안. 바꿀 것이 없으면 null>", "buyer": "<구매 운영자에게, 같은 형식>"},
 "tools": {"hide": ["<도구 이름>"], "show": ["<도구 이름>"], "describe": {"<도구 이름>": "<새 설명 160자 안>"},
           "remove": ["q_<운영자가 만든 도구>"],
           "recommend": {"shop": ["<도구 이름>"], "buyer": ["<도구 이름>"]}, "unrecommend": {"shop": ["<도구 이름>"], "buyer": ["<도구 이름>"]},
           "card": {"shop": ["q_<만든 도구> 또는 뗐던 카드 칸 이름"], "buyer": ["q_<만든 도구> 또는 뗐던 카드 칸 이름"]}, "uncard": {"shop": ["q_<만든 도구> 또는 카드 칸 이름"], "buyer": ["q_<만든 도구> 또는 카드 칸 이름"]}},
 "ask": [{"q": "<조사원에게 맡길 일: 운영자가 못 보는 사실을 세는 q_ 도구를 만들어 저장해 달라는 것 등>", "for": ["shop", "buyer"]}],
 "agents": [{"op": "create|update|retire", "name": "<영소문자_숫자>", "job": "<에이전트의 일 문서 전체>", "wake": {"every_min": <분>, "on_ask": true}, "for": ["shop", "buyer"], "reason": "<근거 숫자>"}],
 "note": "<이번 감사 한 줄 요약>"}
도구 변경은 한 번에 4개까지 (recommend, unrecommend 포함). hide, show, describe, remove는 두 운영자 모두에 걸림. policy, brief_section, guide, tool_search는 추천 목록에서 빼지 못함. policy 키 이름 뒤에 숫자를 붙이면 그 안내가 거절됨.
""" + AGENTS_RULE


# ------------------------------------------------------------ files
def _path(root, rel):
    return os.path.join(root, rel)


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".auditor.tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _atomic_json(path, obj):
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _state(root):
    st = c.load_json(_path(root, STATE), {})
    if not isinstance(st, dict):
        st = {}
    hist = st.get("history")
    st["history"] = (
        [h for h in hist if isinstance(h, dict)] if isinstance(hist, list) else []
    )
    return st


def _hist(st, role):
    """The guidance history list of a role ("history" for the single operator), cleaned and kept in st."""
    key = "history" if role is None else f"history_{role}"
    h = st.get(key)
    st[key] = [x for x in h if isinstance(x, dict)] if isinstance(h, list) else []
    return st[key]


def guidance_rel(role=None):
    """Guidance file of a role beside the single operator's (GUIDANCE)."""
    return GUIDANCE if role is None else GUIDANCE[: -len(".md")] + f"_{role}.md"


def guidance(root, role=None):
    """Current guidance text ("" when none), at most GUIDANCE_CHARS."""
    try:
        with open(_path(root, guidance_rel(role)), encoding="utf-8") as f:
            return f.read()[:GUIDANCE_CHARS]
    except (OSError, ValueError):
        return ""


def tool_state(root):
    """{"hidden": [names], "desc": {name: text}}; a missing or corrupt file is empty."""
    st = c.load_json(_path(root, TOOL_STATE), {})
    st = st if isinstance(st, dict) else {}
    hidden = st.get("hidden") if isinstance(st.get("hidden"), list) else []
    desc = st.get("desc") if isinstance(st.get("desc"), dict) else {}
    return {
        "hidden": [h for h in hidden if isinstance(h, str)],
        "desc": {
            k: v for k, v in desc.items() if isinstance(k, str) and isinstance(v, str)
        },
    }


def card_state(root):
    """The made tools the auditor pinned to each role's state card: {role: [q_ names]}."""
    st = c.load_json(_path(root, TOOL_STATE), {})
    d = st.get("card") if isinstance(st, dict) and isinstance(st.get("card"), dict) else {}
    return {r: [n for n in d.get(r) if isinstance(n, str)] if isinstance(d.get(r), list) else [] for r in ROLES}


def card_off(root):
    """The kernel card parts the auditor took off each role's state card: {role: [brief.CARD_OPTIONAL names]}.
    Never raises (a broken state file takes nothing off)."""
    from kernel import brief

    try:
        st = c.load_json(_path(root, TOOL_STATE), {})
        d = st.get("card_off") if isinstance(st, dict) and isinstance(st.get("card_off"), dict) else {}
        return {r: [n for n in (d.get(r) if isinstance(d.get(r), list) else []) if n in brief.CARD_OPTIONAL[r]]
                for r in ROLES}
    except Exception:  # noqa: BLE001
        return {r: [] for r in ROLES}


def card_lines(root, now, role):
    """State-card lines of the pinned tools of a role: each tool's current output, cut. Never raises."""
    out = []
    try:
        names = card_state(root).get(role, []) if role in ROLES else sorted({n for v in card_state(root).values() for n in v})
    except Exception:  # noqa: BLE001 - a broken state file pins nothing
        return out
    for name in names:
        try:
            text = c.cap(str(made_tools.call(root, name, None, now, count=False)), CARD_CHARS)
            out.append(f"[감사자가 붙인 도구 {name}] (전체는 그 도구를 부르면 나옴)\n{text}")
        except Exception as e:  # noqa: BLE001 - one broken tool is one line
            out.append(f"[감사자가 붙인 도구 {name}] 만들지 못함: {type(e).__name__}")
    return out


def _gaps_block(root, now, hours=24):
    """Facts for finding what the operators cannot see: sentences of recent findings that say something could not be
    checked, counted or joined; the questions open or asked; the tools pinned to the cards."""
    from kernel import questions

    rows = questions.findings(root, since=now - hours * 3600)
    hits = []
    for r in rows:
        for sent in re.split(r"(?<=[.다음임])\s+|\n+", str(r.get("finding") or "")):
            if GAP_RX.search(sent):
                hits.append((r.get("ts") or 0, r.get("id"), r.get("from"), " ".join(sent.split())[:220]))
    ids = {h[1] for h in hits}
    out = [f"최근 {hours}시간 조사 결과 {len(rows)}건 중 '확인하지 못함'류 문장이 있는 것 {len(ids)}건, 그런 문장 {len(hits)}개 "
           f"(최근 것부터 {GAPS_SHOWN}개). 같은 것이 되풀이되면 운영자가 볼 길이 없는 사실임"]
    for ts, qid, frm, sent in sorted(hits, key=lambda h: -h[0])[:GAPS_SHOWN]:
        out.append(f"- #{qid} ({frm}, {c.kst(ts, '%H:%M')}): {sent}")
    asked = Counter(str(q.get("from")) for q in questions.load(root) if (q.get("ts") or 0) >= now - hours * 3600)
    out.append("질문 수(물은 쪽별): " + (", ".join(f"{k} {v}" for k, v in asked.most_common()) or "없음"))
    card = card_state(root)
    out.append("상태 카드에 붙인 도구: " + (", ".join(f"{r} {card[r]}" for r in ROLES if card[r]) or "없음"))
    from kernel import brief

    off = card_off(root)
    for r in ROLES:  # what each operator's card holds now, so the auditor can tell what is not on it
        heads = re.findall(r"^\[([^\]\n]{1,20})\]", brief.build_compact(root, now, now - 3600, role=r, off=off[r]), re.M)
        out.append(f"{ROLE_KO[r]} 상태 카드의 칸: " + ", ".join(dict.fromkeys(re.split(r"\s+(?=\S*\d)", h)[0] for h in heads)))  # the name up to its first number
    out.append("커널이 넣는 칸 중 네가 떼고(uncard) 다시 붙일(card) 수 있는 것: "
               + "; ".join(f"{r} {', '.join(brief.CARD_OPTIONAL[r])}" for r in ROLES)
               + ". 지금 뗀 것: " + ("; ".join(f"{r} {', '.join(off[r])}" for r in ROLES if off[r]) or "없음"))
    return "\n".join(out)


def _board_block(root, now):
    """Other shops' board columns next to ours over about a day (brief.section_turnover), so the auditor can tell a
    gap nobody asked about: its gap finding otherwise starts only from 'could not confirm' sentences."""
    from kernel import brief

    return brief.section(root, "가게 회전", now)


def _outside_block(root):
    """Policy values a loop outside the kernel switches now (brief.section_outside): the operators' edit history and
    the ledger do not hold them, so without this block the auditor cannot tell that they exist."""
    from kernel import brief

    return "\n".join(brief.section_outside(root)) or "없음"


def rec_state(root):
    """The auditor's changes to the recommended lists: {"recommend": {role: [names]}, "unrecommend": {role: [names]}}."""
    st = c.load_json(_path(root, TOOL_STATE), {})
    st = st if isinstance(st, dict) else {}
    out = {}
    for k in ("recommend", "unrecommend"):
        d = st.get(k) if isinstance(st.get(k), dict) else {}
        out[k] = {r: [n for n in d.get(r) if isinstance(n, str)] if isinstance(d.get(r), list) else [] for r in ROLES}
    return out


def recommended(root, role, rs=None):
    """A role's recommended tools in order: the defaults (tools.RECOMMENDED), the q_ tools that role made, the auditor's
    additions; minus the auditor's removals (never CORE_TOOLS)."""
    from kernel import tools

    rs = rec_state(root) if rs is None else rs
    made = []
    if made_tools is not None:
        try:
            made = [t.get("name") for t in made_tools.load(root) if isinstance(t, dict) and t.get("role") == role]
        except Exception:  # noqa: BLE001 - a broken made-tools file is no tool
            made = []
    names = list(tools.RECOMMENDED.get(role, ())) + made + rs["recommend"].get(role, [])
    drop = set(rs["unrecommend"].get(role, [])) - set(CORE_TOOLS)
    return [n for n in dict.fromkeys(names) if isinstance(n, str) and n not in drop]


def _interval_s():
    env = os.environ.get("A2_AUDITOR_INTERVAL_S", "")
    return int(env) if env.isdigit() else INTERVAL_S


def _ended(root):
    try:
        rows = ex.load(root)
    except Exception:  # noqa: BLE001 - an unreadable ledger is no verdict
        rows = []
    return [
        e
        for e in rows
        if isinstance(e, dict)
        and e.get("status") not in (None, "running")
        and _num(e.get("ended_ts"))
    ]


def due(root, now):
    """Reason to run now, or None: first run, interval passed, or VERDICTS_TRIGGER verdicts since the last run."""
    last = _state(root).get("last_run_ts")
    if not _num(last) or last > now:  # a missing, non-finite or future stamp must not silence the auditor
        return "첫 감사"
    if now - last >= _interval_s():
        return "주기 감사"
    n = sum(1 for e in _ended(root) if e["ended_ts"] > last)
    if n >= VERDICTS_TRIGGER and now - last >= MIN_GAP_S:
        return f"실험 판정 {n}건"
    return None


# ------------------------------------------------------------ guidance judging
def _score(root, now, since, role=None):
    if audit is None:
        return None, 0
    rep = audit.report(root, now, since=since, **({"role": role} if role else {}))
    return audit.score(rep), rep.get("wakes") or 0


def _worse(before, after):
    # 11-11: only process mistakes; how much the operator changes (the old idle share) is its strategy
    bm, am = (x if _num(x) else 0 for x in (before.get("mistakes_per_wake"), after.get("mistakes_per_wake")))
    return am > bm * WORSE_MISTAKES_RATIO + WORSE_MISTAKES_SLACK


def judge_guidance(root, now, traj, role=None):
    """Judge the active guidance version once MIN_WAKES compact wakes ran under it: kept, or reverted to the previous
    text when clearly worse. -> the judged version or None. role: that role's guidance, judged on its wakes."""
    st = _state(root)
    tag = {"role": role} if role else {}
    act = next(
        (h for h in reversed(_hist(st, role)) if h.get("status") == "active"), None
    )
    if (
        act is None
        or not _num(act.get("ts"))
        or not isinstance(act.get("before"), dict)
    ):
        return None
    after, wakes = _score(root, now, act["ts"], role)
    if after is None or wakes < MIN_WAKES:
        return None
    act["after"] = after
    thin = not _num(act["before"].get("wakes")) or act["before"]["wakes"] < MIN_WAKES  # no baseline to compare with
    if not thin and _worse(act["before"], after):
        act["status"] = "reverted"
        prev = act.get("prev_text") if isinstance(act.get("prev_text"), str) else ""
        _atomic_write(_path(root, guidance_rel(role)), prev[:GUIDANCE_CHARS])
        traj.append(
            "auditor",
            "auditor_guidance",
            action="reverted",
            id=act.get("id"),
            before=act["before"],
            after=after,
            **tag,
        )
    else:
        act["status"] = "kept"
        traj.append(
            "auditor",
            "auditor_guidance",
            action="kept",
            id=act.get("id"),
            before=act["before"],
            after=after,
            **tag,
        )
    _atomic_json(_path(root, STATE), st)
    return act


# ------------------------------------------------------------ created agents (kernel.agent_registry, kernel.agents)
def _agent_hist(st):
    h = st.get("agent_history")
    st["agent_history"] = [x for x in h if isinstance(x, dict)] if isinstance(h, list) else []
    return st["agent_history"]


def _agent_score(root, now, name, since):
    if audit is None:
        return None, 0
    rep = audit.report(root, now, since=since, agent=name)
    return audit.score(rep), rep.get("wakes") or 0


def judge_agents(root, now, traj):
    """Judge each active agent change of the auditor once MIN_WAKES wakes of that agent ran under it: kept, or put back
    to the previous version (kernel.agent_registry.restore) when its mistakes per wake are clearly worse. A change the
    agent's creator or a later change replaced is superseded. -> the judged records."""
    st = _state(root)
    out = []
    for h in _agent_hist(st):
        if h.get("status") != "active" or not _num(h.get("ts")):
            continue
        row = reg.get(root, h.get("name"))
        if row is None or row["version"] != h.get("version"):
            h["status"] = "superseded"
            out.append(h)
            continue
        after, wakes = _agent_score(root, now, h["name"], h["ts"])
        if after is None or wakes < MIN_WAKES:
            continue
        h["after"] = after
        before = h.get("before") if isinstance(h.get("before"), dict) else {}
        thin = not _num(before.get("wakes")) or before["wakes"] < MIN_WAKES
        if not thin and _worse(before, after):
            h["status"] = "reverted"
            h["restored_version"] = reg.restore(root, h["name"], h.get("prev"), now)
        else:
            h["status"] = "kept"
        traj.append("auditor", "auditor_agent", action=h["status"], id=h.get("id"), name=h["name"], op=h.get("op"),
                    before=before, after=after)
        out.append(h)
    if out:
        _atomic_json(_path(root, STATE), st)
    return out


def _apply_agents(root, now, items, secrets, st, traj):
    """The auditor's agent changes through kernel.agent_registry (creator "auditor": read-only agents, any agent's
    job text and wake, retire). A job text that attaches a number to a policy key is refused like a guidance. Each
    applied create or update is recorded with the previous version and the agent's score before, for judge_agents."""
    applied, refused = [], []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            refused.append({"why": "agents 항목은 객체여야 함"})
            continue
        name = str(it.get("name"))[:40]
        if len(applied) >= AGENT_CHANGES_MAX:
            refused.append({"op": it.get("op"), "name": name, "why": f"한 감사에 에이전트 변경 {AGENT_CHANGES_MAX}개까지"})
            continue
        if isinstance(it.get("job"), str) and tells_value(it["job"]):
            refused.append({"op": it.get("op"), "name": name, "why": "policy 키에 숫자를 붙인 값 지시는 일 문서에 쓸 수 없음"})
            continue
        prev = reg.get(root, it.get("name")) if isinstance(it.get("name"), str) else None
        spec = {k: it[k] for k in ("op", "name", "job", "wake", "powers") if k in it}
        r = reg.apply(root, "auditor", spec, now, traj, secrets, for_=it.get("for") if isinstance(it.get("for"), list) else None)
        if not r.get("ok"):
            refused.append({"op": it.get("op"), "name": name, "why": str(r.get("error"))[:200]})
            continue
        applied.append({"op": r["op"], "name": r["name"], "version": r["version"]})
        if r["op"] == "retire":
            continue
        hist = _agent_hist(st)
        for h in hist:
            if h.get("name") == r["name"] and h.get("status") == "active":
                h["status"] = "superseded"
        since = prev.get("updated_ts") if prev and _num(prev.get("updated_ts")) else now - 24 * 3600
        before, _ = _agent_score(root, now, r["name"], since) if prev and prev["status"] == "live" else (None, 0)
        hist.append({"id": max([h.get("id") for h in hist if _num(h.get("id"))] or [0]) + 1, "ts": now,
                     "name": r["name"], "op": r["op"], "version": r["version"],
                     "prev": {k: prev.get(k) for k in ("job", "wake", "powers", "status")} if prev else None,
                     "before": before, "status": "active",
                     "reason": c.mask(str(it.get("reason") or ""), secrets)[:300]})
        st["agent_history"] = hist[-AGENT_HISTORY_MAX:]
    return {"applied": applied, "refused": refused}


def _agents_block(root, now):
    """One line per created agent with its process facts (kernel.audit.agent_facts); "" when there is none."""
    if audit is None or not reg.load(root):  # no registry: the prompt is as before
        return ""
    out = []
    for f in audit.agent_facts(root, now):
        row = reg.get(root, f["name"]) or {"job": ""}
        out.append(
            f"- {f['name']} [{'살아 있음' if f['status'] == 'live' else '은퇴'}] 만든 쪽 {f['creator']}, 판 {f['version']}, "
            f"권한 {','.join(f['powers'])}, {reg.wake_text(f['wake'])}: {reg.purpose(row['job'])}\n"
            f"  깨움 {f['wakes']}, 점검당 실수 {f['mistakes_per_wake']}, 충돌 {f['crashes']}, 도구 오류 {f['tool_errors']}, "
            f"반복 호출 {f['repeat_calls']}, 근거 없는 숫자 {f['unfounded']}, 거절된 값 {f['refused_items']}, "
            f"권한 밖 항목 {f['outside_powers']}, done 없이 끝남 {f['no_done']}, 모델 오류 {f['model_errors']}, "
            f"조사 결과 {f['findings']} (미완 {f['findings_failed']}), 그 뒤 받는 운영자가 깨어 읽을 수 있었던 것 "
            f"{f['findings_shown']}, 운영자의 agents 도구 조회 {f['looked_up']}")
    return "\n".join(out)


def _agent_history_block(st):
    out = []
    for h in _agent_hist(st)[-HISTORY_SHOWN:]:
        out.append(f"- #{h.get('id')} {c.kst(h['ts']) if _num(h.get('ts')) else '?'} {h.get('op')} {h.get('name')} "
                   f"(판 {h.get('version')}) {h.get('status')}: 전 {json.dumps(h.get('before'), ensure_ascii=False)}, "
                   f"후 {json.dumps(h.get('after'), ensure_ascii=False)}")
    return "\n".join(out) or "없음"


def _context_block(root, now, hours=24):
    """Prompt sizes per agent from the llm rows (prompt_chars, sections) and the compaction rows of the last hours:
    facts for managing context. The kernel's thresholds are not the auditor's to change."""
    calls, comp = {}, {}
    for r in c.tail_json(_path(root, TRAJ), 30_000_000):
        if not _num(r.get("ts")) or r["ts"] < now - hours * 3600:
            continue
        who = str(r.get("agent")) + (f"[{r['role']}]" if isinstance(r.get("role"), str) else "")
        if r.get("kind") == "llm" and _num(r.get("prompt_chars")):
            d = calls.setdefault(who, {"n": 0, "chars": [], "sec": {}, "compact": 0})
            if r.get("purpose") == "compact":
                d["compact"] += 1
                continue
            d["n"] += 1
            d["chars"].append(r["prompt_chars"])
            for k, v in (r.get("sections") if isinstance(r.get("sections"), dict) else {}).items():
                if _num(v):
                    d["sec"].setdefault(str(k)[:30], []).append(v)
        elif r.get("kind") == "compact":
            d = comp.setdefault(who, {"ok": 0, "failed": 0, "before": [], "after": []})
            d["ok" if r.get("status") == "ok" else "failed"] += 1
            if r.get("status") == "ok" and _num(r.get("chars_before")) and _num(r.get("chars_after")):
                d["before"].append(r["chars_before"])
                d["after"].append(r["chars_after"])
    mean = lambda v: round(sum(v) / len(v)) if v else 0
    out = []
    for who in sorted(set(calls) | set(comp)):
        d = calls.get(who) or {"n": 0, "chars": [], "sec": {}, "compact": 0}
        line = f"- {who}: 호출 {d['n']}"
        if d["chars"]:
            line += f", 프롬프트 평균 {mean(d['chars']):,}자 (최대 {max(d['chars']):,})"
            top = sorted(d["sec"].items(), key=lambda kv: -mean(kv[1]))[:CONTEXT_SECTIONS]
            if top:
                line += ", 절별 평균: " + ", ".join(f"{k} {mean(v):,}" for k, v in top)
        if d["compact"]:
            line += f", 간추림 호출 {d['compact']}"
        k = comp.get(who)
        if k:
            line += f"; 간추림 성공 {k['ok']}, 실패 {k['failed']}" + (
                f", 평균 {mean(k['before']):,}자에서 {mean(k['after']):,}자로" if k["before"] else "")
        out.append(line)
    return "\n".join(out) or "없음"


# ------------------------------------------------------------ prompt
def _nonzero(obj):
    if isinstance(obj, dict):
        out = {k: _nonzero(v) for k, v in obj.items()}
        return {
            k: v for k, v in out.items() if v not in (0, 0.0, None, "", [], {}, False)
        }
    if isinstance(obj, list):
        return [_nonzero(v) for v in obj]
    return obj


def _default_listed(root):
    try:
        from kernel import tools

        names = [
            n
            for n, (_, agents) in tools.TOOLS.items()
            if "operator" in agents and n not in HARNESS_TOOLS
        ]
    except Exception:  # noqa: BLE001 - no tool registry is an empty list
        names = []
    return names + [n for n, _ in _made_listing(root) if n not in names]


def _made_listing(root):
    if made_tools is None:
        return []
    try:
        return [(n, d) for n, d in made_tools.listing(root) if isinstance(n, str)]
    except Exception:  # noqa: BLE001 - a broken made-tools file is no tool
        return []


def _made_names(root):
    if made_tools is None:
        return set()
    try:
        return {t.get("name") for t in made_tools.load(root) if isinstance(t, dict)}
    except Exception:  # noqa: BLE001
        return set()


def _compact_wakes(root, since):
    """Compact operator wake rows with ts >= since, each with the tool names called before it, in file order."""
    out, names = [], []
    for r in c.tail_json(_path(root, TRAJ), 30_000_000):
        if r.get("agent") != "operator" or not _num(r.get("ts")):
            continue
        if r.get("kind") == "tool":
            names.append(r.get("name") if isinstance(r.get("name"), str) else "?")
        elif r.get("kind") == "operator_wake":
            res = r.get("result")
            if isinstance(res, dict) and _num(res.get("calls")) and r["ts"] >= since:
                out.append(dict(r, _tools=names))
            names = []
    return out


def _wake_text(w):
    res = w.get("result") or {}
    who = f"[{w['role']}] " if w.get("role") in ROLES else ""
    pol = []
    for p in res.get("policy") if isinstance(res.get("policy"), list) else []:
        if isinstance(p, dict):
            item = {"key": p.get("key", "?"), "ok": p.get("ok") is True}
            if p.get("error"):
                item["error"] = str(p["error"])[:120]
            pol.append(item)
    return "\n".join(
        [
            f"- {who}{c.kst(w['ts'])} 상태 {w.get('status')}, 도구: {', '.join(w['_tools']) or '없음'}",
            f"  요약: {str(res.get('note') or '')[:WAKE_NOTE_CHARS] or '없음'}",
            f"  policy: {json.dumps(pol, ensure_ascii=False) if pol else '없음'}",
        ]
    )


def _notes(root, role=None):
    try:
        with open(_path(root, NOTES if role is None else NOTES[: -len(".md")] + f"_{role}.md"), encoding="utf-8") as f:
            return f.read()[:NOTES_SHOWN]
    except (OSError, ValueError):
        return ""


def _tool_block(root, now, listed, ts):
    made = dict(_made_listing(root))
    if audit is not None:
        rows = audit.tool_table(root, now, 24, listed=listed)
    else:
        rows = [
            {
                "name": n,
                "uses": 0,
                "wakes": 0,
                "obs_chars_mean": 0,
                "repeats": 0,
                "listed": True,
            }
            for n in listed
        ]
    names = {r.get("name") for r in rows}
    rows += [
        {
            "name": n,
            "uses": 0,
            "wakes": 0,
            "obs_chars_mean": 0,
            "repeats": 0,
            "listed": False,
        }
        for n in ts["hidden"]
        if n not in names
    ]
    out = ["이름 | 쓴 횟수 | 쓴 점검 | 관찰 평균 글자 | 반복 | 오류 답 | 잘린 채 봄 | 읽고 안 씀 | 목록 | 운영자가 만듦"]
    for r in rows:
        n = r.get("name")
        state = (
            "숨김" if n in ts["hidden"] else "보임" if r.get("listed") else "목록 밖"
        )
        line = f"{n} | {r.get('uses', 0)} | {r.get('wakes', 0)} | {r.get('obs_chars_mean', 0)} | {r.get('repeats', 0)} | {r.get('errors', 0)}{(' (' + str(r.get('last_error'))[:60] + ')') if r.get('errors') else ''} | {r.get('cut', 0)} | {r.get('uncited', 0)} | {state}"
        line += (
            f" | 예: {' '.join(str(made[n]).split())[:DESC_CHARS]}"
            if n in made
            else " | 아니오"
        )
        if n in ts["desc"]:
            line += f" | 고친 설명: {ts['desc'][n]}"
        out.append(line)
    return "\n".join(out)


def _history_block(hist):
    out = []
    for h in hist[-HISTORY_SHOWN:]:
        out.append(
            f"- #{h.get('id')} {c.kst(h['ts']) if _num(h.get('ts')) else '?'} {h.get('status')}: "
            f"전 {json.dumps(h.get('before'), ensure_ascii=False)}, 후 {json.dumps(h.get('after'), ensure_ascii=False)}"
            f"\n  {' '.join(str(h.get('text') or '').split())[:200]}"
        )
    return "\n".join(out) or "없음"


def _period(root, now, since, listed, roles=None):
    """What the kernel counted over one audit period and the per-tool rows, for the auditor's memory. roles: also each
    role's numbers under nums["roles"]."""
    if audit is None:
        return {}, {}
    r = audit.report(root, now, since=since, listed=listed)
    nums = dict(audit.score(r))
    for k in ("repeat_calls", "tool_errors", "uncited_reads", "cut_reads"):
        nums[k] = r.get(k, 0)
    nums["unfounded"] = len(r.get("unfounded_numbers") or [])
    if roles:
        nums["roles"] = {}
        for role in roles:
            rr = audit.report(root, now, since=since, listed=recommended(root, role), role=role)
            nums["roles"][role] = dict(audit.score(rr), **{k: rr.get(k, 0) for k in (
                "repeat_calls", "tool_errors", "outside_calls", "searches", "search_zero")})
    return nums, r.get("tools") or {}


def _remember(st, now, out, nums, rows):
    """Append this run to the auditor's memory: what it said and changed, and the numbers it was looking at."""
    tools = [
        dict(ch, before={k: (rows.get(ch.get("name")) or {}).get(k, 0) for k in ("uses", "errors", "uncited", "cut")})
        for ch in out["tools"]["applied"]
    ]
    runs = st.get("runs") if isinstance(st.get("runs"), list) else []
    runs.append(
        {
            "ts": now,
            "nums": nums,
            "findings": [str(x)[:160] for x in out["findings"]][:4],
            "note": out["note"][:160],
            "guidance_changed": out["guidance_changed"],
            "tools": tools,
            **({"agents": out["agents"]["applied"]} if (out.get("agents") or {}).get("applied") else {}),
            **({"ask": out["ask"]["applied"]} if (out.get("ask") or {}).get("applied") else {}),
        }
    )
    st["runs"] = runs[-RUNS_MAX:]


def _runs_block(st):
    """The auditor's memory as prompt text, oldest first: each run's numbers are the period BEFORE that run, so the
    effect of a run shows in the numbers of the next one."""
    runs = [r for r in (st.get("runs") if isinstance(st.get("runs"), list) else []) if isinstance(r, dict)]
    out = []
    for r in runs[-RUNS_SHOWN:]:
        out.append(
            f"- {c.kst(r['ts']) if _num(r.get('ts')) else '?'} 그때까지의 숫자 "
            f"{json.dumps(r.get('nums'), ensure_ascii=False, separators=(',', ':'))}"
            f"{', 안내 바꿈' if r.get('guidance_changed') else ''}"
        )
        out += [f"  짚은 것: {x}" for x in r.get("findings") or []]
        out += [
            f"  도구 {t.get('op')} {t.get('name')} (그때 {json.dumps(t.get('before'), ensure_ascii=False)})"
            for t in r.get("tools") or []
            if isinstance(t, dict)
        ]
        out += [f"  에이전트 {a.get('op')} {a.get('name')} (판 {a.get('version')})"
                for a in r.get("agents") if isinstance(a, dict)] if isinstance(r.get("agents"), list) else []
        if r.get("note"):
            out.append(f"  메모: {r['note']}")
    return "\n".join(out)[-RUNS_CHARS:] or "없음 (첫 감사)"


def _fit(head, blocks, tail, limit=PROMPT_CHARS, sizes=None):
    """Join head, (header, body) blocks and tail; trim the longest bodies until the whole fits in limit. sizes (a
    dict) gets the chars of each part: "job", each header's name (up to " ("), "답 형식"."""
    blocks = [[h, b] for h, b in blocks]

    def total():
        return len("\n\n".join([head] + [f"{h}\n\n{b}" for h, b in blocks] + [tail]))

    mark = "\n[잘림]"
    while total() > limit:
        i = max(range(len(blocks)), key=lambda k: len(blocks[k][1]))
        body = blocks[i][1]
        if len(body) <= len(mark):
            break
        keep = max(0, len(body) - (total() - limit) - len(mark))
        blocks[i][1] = body[:keep] + mark
    if sizes is not None:
        sizes["job"] = len(head) + 2
        for h, b in blocks:
            name = h[3:].split(" (", 1)[0].strip()
            sizes[name] = sizes.get(name, 0) + len(h) + len(b) + 4
        sizes["답 형식"] = len(tail)
    return "\n\n".join([head] + [f"{h}\n\n{b}" for h, b in blocks] + [tail])


def _role_tool_block(root, now, role):
    if audit is None:
        return "감사 모듈 없음"
    rows, s = audit.role_tool_table(root, now, role, recommended(root, role))
    out = ["이름 | 호출 | 추천 목록 밖 호출 | 추천 | 오류 답 | 잘린 채 봄 | 읽고 안 씀"]
    out += [f"{r['name']} | {r['calls']} | {r['outside']} | {'예' if r['recommended'] else '아니오'} | {r['errors']} | "
            f"{r['cut']} | {r['uncited']}" for r in rows]
    out.append(f"tool_search 질의 {s['searches']}회, 결과 0개 {s['search_zero']}회"
               + (f": {', '.join(s['queries'])}" if s["queries"] else ""))
    return "\n".join(out)


def _role_blocks(root, now, st):
    """The per-role blocks of a roles run: [감사] lines, tool table, notes, guidance and its history."""
    out = []
    for r in ROLES:
        who = ROLE_KO[r]
        if audit is not None:
            out.append((f"## {who} 감사 보고 (최근 24시간, 그 운영자의 점검만)",
                        "\n".join(audit.lines(root, now, 24, listed=recommended(root, r), role=r)) or "없음"))
        out += [
            (f"## {who} 도구 표 (최근 24시간)", _role_tool_block(root, now, r)),
            (f"## {who} 메모 (앞 800자)", _notes(root, r) or "없음"),
            (f"## {who} 지금 안내 (guidance.{r})", guidance(root, r) or "없음"),
            (f"## {who} 안내 이력 (최근 4판, 그 운영자의 점검으로 잰 전후 점수)", _history_block(_hist(st, r))),
        ]
    return out


def build_prompt(root, now, secrets=(), listed=None, roles=None, sizes=None):
    """The auditor prompt: job text, the data blocks, the answer schema; masked, at most PROMPT_CHARS. roles: the
    single-operator notes and guidance blocks give way to the per-role blocks. sizes (a dict) gets the chars by
    section."""
    st = _state(root)
    last = st.get("last_run_ts") if _num(st.get("last_run_ts")) else now - 24 * 3600
    listed = list(listed) if listed is not None else _default_listed(root)
    ts = tool_state(root)
    try:
        with open(JOB, encoding="utf-8") as f:
            job = f.read()
    except OSError:
        job = ""
    if audit is not None:
        audit_lines = "\n".join(audit.lines(root, now, 24, listed=listed)) or "없음"
        rep = json.dumps(
            _nonzero(audit.report(root, now, since=last, listed=listed)),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    else:
        audit_lines, rep = "감사 모듈 없음", "{}"
    exp = (
        "\n".join(ex.lines(root, now))
        + "\n"
        + json.dumps(ex.summary(root, now), ensure_ascii=False)
    )
    wakes = _compact_wakes(root, now - WAKE_LOOKBACK_S)[-WAKES_SHOWN:]
    blocks = [
        ("## 감사 보고 (최근 24시간, 커널이 셈)", audit_lines),
        (f"## 감사 숫자 (지난 감사 {c.kst(last)} 이후, 0 아닌 항목만)", rep),
        ("## 도구 표 (최근 24시간)", _tool_block(root, now, listed, ts)),
        ("## 정보 빈칸 (조사 결과에서 커널이 찾은 문장, 해석 없음)", _safe(lambda: _gaps_block(root, now))),
        ("## 다른 가게와 우리 (순위표의 가게 열, 커널이 셈. 해석 없음)", _safe(lambda: _board_block(root, now))),
        ("## 커널 밖에서 바뀌는 정책 값 (시도 1 실험 루프의 상태 파일, 해석 없음)", _safe(lambda: _outside_block(root))),
        ("## 실험 기록 (커널 판정)", exp),
        (
            "## 코드 수정 (운영자가 규칙 코드를 고친 기록)",
            ("\n".join(code_edit.history_lines(root, 4) + code_edit.refusal_lines(root, 3))
             if code_edit is not None else "") or "없음",
        ),
        ("## 최근 점검 (운영자)", "\n".join(_wake_text(w) for w in wakes) or "없음"),
        ("## 프롬프트 크기 (최근 24시간, 에이전트별 모델 호출과 간추림. 커널이 잼)", _context_block(root, now)),
    ]
    agents = _agents_block(root, now)
    if agents:
        blocks += [("## 에이전트 (만든 에이전트별 과정 숫자, 최근 24시간. 커널이 셈)", agents),
                   ("## 네 에이전트 변경 이력 (최근 4건, 그 에이전트의 점검으로 잰 전후 점수)", _agent_history_block(st))]
    if roles:
        blocks += _role_blocks(root, now, st)
    else:
        blocks += [
            ("## 운영자 메모 (앞 800자)", _notes(root) or "없음"),
            ("## 지금 안내 (guidance)", guidance(root) or "없음"),
            ("## 안내 이력 (최근 4판, 커널이 잰 전후 점수)", _history_block(st["history"])),
        ]
    blocks.append(("## 네 지난 감사 (네 기억. 각 줄의 숫자는 그 감사 직전 구간이라, 조치의 결과는 다음 줄 숫자에 나타남)", _runs_block(st)))
    blocks = [(h, c.mask(b, secrets)) for h, b in blocks]
    return _fit(c.mask(job, secrets), blocks, "## 답 형식\n\n" + (SCHEMA_ROLES if roles else SCHEMA), sizes=sizes)


def _safe(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a block that cannot be built is one line
        return f"만들지 못함: {type(e).__name__}"


# ------------------------------------------------------------ apply
def _apply_ask(root, now, items, secrets):
    """The auditor's questions to the investigator (which can run analysis code and save q_ tools)."""
    from kernel import questions

    applied, refused = [], []
    for it in items if isinstance(items, list) else []:
        q = it.get("q") if isinstance(it, dict) else None
        if not isinstance(q, str) or not q.strip():
            refused.append({"why": "q는 비어 있지 않은 글이어야 함"})
        elif tells_value(q):
            refused.append({"q": q[:60], "why": "policy 키에 숫자를 붙인 값 지시는 질문에 쓸 수 없음"})
        elif len(applied) >= ASKS_MAX:
            refused.append({"q": q[:60], "why": f"한 감사에 질문 {ASKS_MAX}개까지"})
        else:
            r = questions.add(root, "auditor", c.mask(q.strip(), secrets)[: questions.Q_CHARS], now, for_=it.get("for"))
            (applied if r.get("ok") else refused).append({"id": r["id"]} if r.get("ok") else {"q": q[:60], "why": r.get("error")})
    return {"applied": applied, "refused": refused}


def tells_value(text):
    """True when text attaches a number to a policy key name (within 25 characters on the same line)."""
    return bool(VALUE_RX.search(text))


def _apply_guidance(root, now, text, secrets, st, role=None):
    if text is None:
        return {"changed": False}
    if not isinstance(text, str) or not text.strip():
        return {"changed": False, "refused": "guidance는 비어 있지 않은 글이어야 함"}
    text = c.mask(text.strip(), secrets)[:GUIDANCE_CHARS]
    if tells_value(text):
        return {
            "changed": False,
            "refused": "policy 키에 숫자를 붙인 값 지시는 안내에 쓸 수 없음",
        }
    cur = guidance(root, role)
    if text.strip() == cur.strip():
        return {"changed": False, "unchanged": True}
    hist = _hist(st, role)
    prev_ts = (
        hist[-1].get("ts") if hist and _num(hist[-1].get("ts")) else now - 24 * 3600
    )
    before, _ = _score(root, now, prev_ts, role)
    for h in hist:
        if h.get("status") == "active":
            h["status"] = "superseded"
    nid = max([h.get("id") for h in hist if _num(h.get("id"))] or [0]) + 1
    hist.append(
        {
            "id": nid,
            "ts": now,
            "text": text,
            "prev_text": cur,
            "before": before,
            "status": "active",
        }
    )
    st["history" if role is None else f"history_{role}"] = hist[-HISTORY_MAX:]
    _atomic_write(_path(root, guidance_rel(role)), text)
    return {"changed": True, "id": nid, "chars": len(text)}


def _names(v):
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _apply_tools(root, now, ask, listed, secrets, traj, roles=None):
    applied, refused = [], []
    if not isinstance(ask, dict):
        return {"applied": applied, "refused": refused}
    ts = tool_state(root)
    rs = rec_state(root) if roles else None
    hidden, desc = list(ts["hidden"]), dict(ts["desc"])
    made = _made_names(root)

    def room(op, name):
        if len(applied) >= TOOL_CHANGES_MAX:
            refused.append(
                {
                    "op": op,
                    "name": name,
                    "why": f"한 감사에 도구 변경 {TOOL_CHANGES_MAX}개까지",
                }
            )
            return False
        return True

    for name in _names(ask.get("hide")):
        if name in CORE_TOOLS:
            refused.append(
                {"op": "hide", "name": name, "why": "핵심 도구는 숨기지 못함"}
            )
        elif name.startswith("q_"):
            refused.append(
                {
                    "op": "hide",
                    "name": name,
                    "why": "q_ 도구는 숨기지 않고 remove로 지움",
                }
            )
        elif name in hidden:
            refused.append({"op": "hide", "name": name, "why": "이미 숨김"})
        elif name not in listed:
            refused.append({"op": "hide", "name": name, "why": "목록에 없는 도구"})
        elif room("hide", name):
            hidden.append(name)
            applied.append({"op": "hide", "name": name})
    for name in _names(ask.get("show")):
        if name not in hidden:
            refused.append({"op": "show", "name": name, "why": "숨긴 도구가 아님"})
        elif room("show", name):
            hidden.remove(name)
            applied.append({"op": "show", "name": name})
    d = ask.get("describe") if isinstance(ask.get("describe"), dict) else {}
    for name, text in d.items():
        if name not in listed:
            refused.append({"op": "describe", "name": name, "why": "목록에 없는 도구"})
        elif not isinstance(text, str) or not text.strip():
            refused.append(
                {
                    "op": "describe",
                    "name": name,
                    "why": "설명은 비어 있지 않은 글이어야 함",
                }
            )
        elif tells_value(text):
            refused.append({"op": "describe", "name": name, "why": "policy 키에 숫자를 붙인 값 지시는 설명에 쓸 수 없음"})
        elif room("describe", name):
            desc[name] = c.mask(" ".join(text.split()), secrets)[:DESC_CHARS]
            applied.append({"op": "describe", "name": name})
    for name in _names(ask.get("remove")):
        if not name.startswith("q_"):
            refused.append(
                {"op": "remove", "name": name, "why": "운영자가 만든 q_ 도구만 지움"}
            )
        elif name not in made:
            refused.append(
                {"op": "remove", "name": name, "why": "운영자가 만든 도구 중에 없음"}
            )
        elif room("remove", name):
            try:
                r = made_tools.remove(root, name, now, traj=traj, by="auditor")
            except Exception as e:  # noqa: BLE001 - a failed removal is a refusal
                r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
            if isinstance(r, dict) and r.get("ok"):
                desc.pop(name, None)
                applied.append({"op": "remove", "name": name})
            else:
                refused.append(
                    {
                        "op": "remove",
                        "name": name,
                        "why": str((r or {}).get("error") or "remove 실패"),
                    }
                )
    for op in ("recommend", "unrecommend") if roles else ():
        d = ask.get(op) if isinstance(ask.get(op), dict) else {}
        for role, names in d.items():
            for name in _names(names):
                cur = recommended(root, role, rs) if role in roles else []
                if role not in roles:
                    refused.append({"op": op, "role": str(role)[:20], "name": name, "why": "역할은 shop 또는 buyer"})
                elif op == "recommend" and name not in listed:
                    refused.append({"op": op, "role": role, "name": name, "why": "목록에 없는 도구"})
                elif op == "recommend" and name in cur:
                    refused.append({"op": op, "role": role, "name": name, "why": "이미 추천 목록에 있음"})
                elif op == "unrecommend" and name in CORE_TOOLS:
                    refused.append({"op": op, "role": role, "name": name, "why": "핵심 도구는 추천 목록에서 빼지 못함"})
                elif op == "unrecommend" and name not in cur:
                    refused.append({"op": op, "role": role, "name": name, "why": "추천 목록에 없는 도구"})
                elif room(op, name):
                    undo, keep = ("unrecommend", "recommend") if op == "recommend" else ("recommend", "unrecommend")
                    rs[undo][role] = [n for n in rs[undo][role] if n != name]
                    if (name in recommended(root, role, rs)) != (op == "recommend"):
                        rs[keep][role].append(name)
                    applied.append({"op": op, "role": role, "name": name})
    from kernel import brief

    card, off = card_state(root), card_off(root)
    for op in ("card", "uncard") if roles else ():
        d = ask.get(op) if isinstance(ask.get(op), dict) else {}
        for role, names in d.items():
            for name in _names(names):
                if role not in roles:
                    refused.append({"op": op, "role": str(role)[:20], "name": name, "why": "역할은 shop 또는 buyer"})
                elif name in brief.CARD_OPTIONAL.get(role, ()):  # a kernel card part: uncard takes it off, card puts it back
                    if (name in off[role]) == (op == "uncard"):
                        refused.append({"op": op, "role": role, "name": name,
                                        "why": "이미 떼어져 있음" if op == "uncard" else "이미 붙어 있음"})
                    elif room(op, name):
                        off[role] = [n for n in off[role] if n != name] + ([name] if op == "uncard" else [])
                        applied.append({"op": op, "role": role, "name": name})
                elif op == "card" and name not in _made_names(root):
                    refused.append({"op": op, "role": role, "name": name, "why": "만든 도구(q_) 중에 없음"})
                elif op == "card" and name in card[role]:
                    refused.append({"op": op, "role": role, "name": name, "why": "이미 붙어 있음"})
                elif op == "card" and len(card[role]) >= CARD_MAX:
                    refused.append({"op": op, "role": role, "name": name, "why": f"한 카드에 도구 {CARD_MAX}개까지. 먼저 uncard로 뗌"})
                elif op == "uncard" and name not in card[role]:
                    refused.append({"op": op, "role": role, "name": name, "why": "붙어 있지 않음"})
                elif room(op, name):
                    card[role] = [n for n in card[role] if n != name] + ([name] if op == "card" else [])
                    applied.append({"op": op, "role": role, "name": name})
    for role in ROLES:  # a removed tool leaves the cards
        card[role] = [n for n in card[role] if n in _made_names(root)]
    if applied:
        raw = c.load_json(_path(root, TOOL_STATE), {})
        raw = raw if isinstance(raw, dict) else {}
        raw.update(hidden=hidden, desc=desc, card=card, card_off=off)
        if rs is not None:
            raw.update(rs)
        _atomic_json(_path(root, TOOL_STATE), raw)
    return {"applied": applied, "refused": refused}


# ------------------------------------------------------------ run
def _finish(root, traj, now, st, out):
    st["last_run_ts"] = now
    st["verdicts_seen"] = len(_ended(root))
    _atomic_json(_path(root, STATE), st)
    traj.append("auditor", "auditor_run", **out)
    return out


def run(
    root, terra, traj, now, secrets=(), listed=None, effort="medium", timeout_s=300, roles=None
):
    """One audit: judge the active guidance, one model call, apply guarded changes. -> the auditor_run row fields.
    roles: one run for both role operators (guidance judged and written per role)."""
    reason = due(root, now) or "수동 실행"
    if roles:
        judged = {r: judge_guidance(root, now, traj, role=r) for r in roles}
    else:
        judged = judge_guidance(root, now, traj)
    try:
        agents_judged = judge_agents(root, now, traj)
    except Exception:  # noqa: BLE001 - a broken registry must not stop the audit
        agents_judged = []
    st = _state(root)
    out = {
        "reason": reason,
        "status": "",
        "prompt_chars": 0,
        "findings": [],
        "guidance_changed": False,
        "tools": {"applied": [], "refused": []},
        "note": "",
    }
    if roles:
        out["judged"] = {r: {"id": j.get("id"), "status": j.get("status")} for r, j in judged.items() if j is not None}
    elif judged is not None:
        out["judged"] = {"id": judged.get("id"), "status": judged.get("status")}
    if agents_judged:
        out["agents_judged"] = [{"id": h.get("id"), "name": h.get("name"), "status": h.get("status")} for h in agents_judged]
    if _num(st.get("last_run_ts")) and not _compact_wakes(root, st["last_run_ts"]):
        return _finish(
            root,
            traj,
            now,
            st,
            dict(out, status="skipped", note="지난 감사 뒤 운영자 점검 없음"),
        )
    listed = list(listed) if listed is not None else _default_listed(root)
    sizes = {}
    prompt = build_prompt(root, now, secrets, listed, sizes=sizes, **({"roles": roles} if roles else {}))
    out["prompt_chars"] = len(prompt)
    try:
        nums, rows = _period(root, now, st.get("last_run_ts") if _num(st.get("last_run_ts")) else now - 24 * 3600, listed,
                             roles)
    except Exception:  # noqa: BLE001 - memory is best effort
        nums, rows = {}, {}
    try:
        with llm.sections(sizes):
            ans = parse_last_object(
                terra.complete(prompt, agent="auditor", timeout_s=timeout_s, effort=effort)
            )
    except Exception as e:  # noqa: BLE001 - any model-side failure ends this audit with nothing applied
        return _finish(
            root,
            traj,
            now,
            st,
            dict(out, status="model_error", error=f"{type(e).__name__}: {e}"[:300]),
        )
    f = ans.get("findings")
    out["findings"] = [
        c.mask(str(x), secrets)[:300] for x in (f if isinstance(f, list) else [])
    ][:FINDINGS_MAX]
    out["note"] = c.mask(str(ans.get("note") or ""), secrets)[:300]
    if roles:
        ask = ans.get("guidance") if isinstance(ans.get("guidance"), dict) else {}
        g = {r: _apply_guidance(root, now, ask.get(r), secrets, st, role=r) for r in roles}
        if ans.get("guidance") is not None and not isinstance(ans.get("guidance"), dict):
            g["refused"] = "guidance는 역할별 객체 {shop, buyer}여야 함"
        out["guidance_changed"] = any(v.get("changed") for v in g.values() if isinstance(v, dict))
    else:
        g = _apply_guidance(root, now, ans.get("guidance"), secrets, st)
        out["guidance_changed"] = g["changed"]
    out["guidance"] = g
    out["tools"] = _apply_tools(root, now, ans.get("tools"), listed, secrets, traj, roles)
    if "agents" in ans:
        out["agents"] = _apply_agents(root, now, ans.get("agents"), secrets, st, traj)
    if "ask" in ans:
        out["ask"] = _apply_ask(root, now, ans.get("ask"), secrets)
    _remember(st, now, out, nums, rows)
    return _finish(root, traj, now, st, dict(out, status="done"))


_RETRY_AT = {}  # root -> earliest next try after a crash (the state file may be the thing that is broken)


def maybe_run(root, terra, traj, now, secrets=(), listed=None, force=False, roles=None):
    """run() when due() or force, else None. Never raises: a crash is returned and logged as an auditor_run row,
    and the next try waits MIN_GAP_S so a broken state file cannot call the model on every poll."""
    try:
        if not force and (now < _RETRY_AT.get(root, 0) or not due(root, now)):
            return None
        return run(root, terra, traj, now, secrets=secrets, listed=listed, **({"roles": roles} if roles else {}))
    except Exception as e:  # noqa: BLE001 - the auditor must never stop the operator daemon
        _RETRY_AT[root] = now + MIN_GAP_S
        out = {"status": "crash", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        try:
            traj.append("auditor", "auditor_run", **out)
        except Exception:  # noqa: BLE001
            pass
        return out
