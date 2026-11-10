"""Auditor: the meta-harness over the operator model (human code). It checks how the operator works, not what it decides.

One model call per run reads the audit counts (kernel.audit), the tool table, the experiment ledger, the last compact
wakes, the operator's notes and the guidance history, and may answer with a new working-method guidance for the
operator and tool list changes (hide, show, describe, remove operator-made q_ tools). It never sets policy values: a
guidance that attaches a number to a policy key is refused. Each guidance version is judged by code
(judge_guidance): mistakes per wake and idle share before and after, reverted to the previous text when clearly worse.
Missing sibling modules (kernel.audit, kernel.made_tools) count as no data. Corrupt state files read as empty.
"""

import json
import math
import os
import re
import tempfile

from kernel import common as c
from kernel import experiments as ex
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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB = os.path.join(HERE, "AUDITOR_JOB.md")
GUIDANCE = "a2/operator_guidance.md"
GUIDANCE_CHARS = 1500
TOOL_STATE = "a2/tool_state.json"  # {"hidden": [names], "desc": {name: text}}
STATE = "a2/auditor_state.json"  # last_run_ts, verdicts_seen, guidance history
NOTES = "a2/operator_notes.md"
TRAJ = "a2/trajectory.jsonl"
CORE_TOOLS = ("policy", "brief_section", "guide")
HARNESS_TOOLS = (
    "versions",
    "harness_read",
    "replay_probe",
)  # operator.HARNESS_TOOLS: not listed in compact mode
INTERVAL_S = 6 * 3600  # env A2_AUDITOR_INTERVAL_S overrides
MIN_GAP_S = 2 * 3600
VERDICTS_TRIGGER = 3
MIN_WAKES = 4  # wakes needed under a guidance before it is judged
WORSE_MISTAKES_RATIO = (
    1.25  # revert when after.mistakes_per_wake > before * ratio + slack
)
WORSE_MISTAKES_SLACK = 0.5
WORSE_IDLE_DELTA = 0.25  # revert when after.idle_share > before.idle_share + delta
HISTORY_MAX = 12
HISTORY_SHOWN = 4
DESC_CHARS = 160
TOOL_CHANGES_MAX = 4
PROMPT_CHARS = 14_000
NOTES_SHOWN = 800
WAKES_SHOWN = 3
WAKE_NOTE_CHARS = 300
WAKE_LOOKBACK_S = 72 * 3600
FINDINGS_MAX = 5
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
SCHEMA = """JSON 객체 하나로만 답함. 모든 항목은 선택임:
{"findings": ["<실수와 근거 숫자>", ... 5개까지],
 "guidance": "<운영자에게 줄 새 안내 전체, 1,500자 안. 바꿀 것이 없으면 null>",
 "tools": {"hide": ["<도구 이름>"], "show": ["<도구 이름>"], "describe": {"<도구 이름>": "<새 설명 160자 안>"},
           "remove": ["q_<운영자가 만든 도구>"]},
 "note": "<이번 감사 한 줄 요약>"}
도구 변경은 한 번에 4개까지. policy 키 이름 뒤에 숫자를 붙이면 안내 전체가 거절됨."""


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


def guidance(root):
    """Current guidance text ("" when none), at most GUIDANCE_CHARS."""
    try:
        with open(_path(root, GUIDANCE), encoding="utf-8") as f:
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
def _score(root, now, since):
    if audit is None:
        return None, 0
    rep = audit.report(root, now, since=since)
    return audit.score(rep), rep.get("wakes") or 0


def _worse(before, after):
    bm, am, bi, ai = (
        x if _num(x) else 0
        for x in (before.get("mistakes_per_wake"), after.get("mistakes_per_wake"),
                  before.get("idle_share"), after.get("idle_share"))
    )
    return (
        am > bm * WORSE_MISTAKES_RATIO + WORSE_MISTAKES_SLACK
        or ai > bi + WORSE_IDLE_DELTA
    )


def judge_guidance(root, now, traj):
    """Judge the active guidance version once MIN_WAKES compact wakes ran under it: kept, or reverted to the previous
    text when clearly worse. -> the judged version or None."""
    st = _state(root)
    act = next(
        (h for h in reversed(st["history"]) if h.get("status") == "active"), None
    )
    if (
        act is None
        or not _num(act.get("ts"))
        or not isinstance(act.get("before"), dict)
    ):
        return None
    after, wakes = _score(root, now, act["ts"])
    if after is None or wakes < MIN_WAKES:
        return None
    act["after"] = after
    thin = not _num(act["before"].get("wakes")) or act["before"]["wakes"] < MIN_WAKES  # no baseline to compare with
    if not thin and _worse(act["before"], after):
        act["status"] = "reverted"
        prev = act.get("prev_text") if isinstance(act.get("prev_text"), str) else ""
        _atomic_write(_path(root, GUIDANCE), prev[:GUIDANCE_CHARS])
        traj.append(
            "auditor",
            "auditor_guidance",
            action="reverted",
            id=act.get("id"),
            before=act["before"],
            after=after,
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
        )
    _atomic_json(_path(root, STATE), st)
    return act


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
    pol = []
    for p in res.get("policy") if isinstance(res.get("policy"), list) else []:
        if isinstance(p, dict):
            item = {"key": p.get("key", "?"), "ok": p.get("ok") is True}
            if p.get("error"):
                item["error"] = str(p["error"])[:120]
            pol.append(item)
    return "\n".join(
        [
            f"- {c.kst(w['ts'])} 상태 {w.get('status')}, 도구: {', '.join(w['_tools']) or '없음'}",
            f"  요약: {str(res.get('note') or '')[:WAKE_NOTE_CHARS] or '없음'}",
            f"  policy: {json.dumps(pol, ensure_ascii=False) if pol else '없음'}",
        ]
    )


def _notes(root):
    try:
        with open(_path(root, NOTES), encoding="utf-8") as f:
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
    out = ["이름 | 쓴 횟수 | 쓴 점검 | 관찰 평균 글자 | 반복 | 목록 | 운영자가 만듦"]
    for r in rows:
        n = r.get("name")
        state = (
            "숨김" if n in ts["hidden"] else "보임" if r.get("listed") else "목록 밖"
        )
        line = f"{n} | {r.get('uses', 0)} | {r.get('wakes', 0)} | {r.get('obs_chars_mean', 0)} | {r.get('repeats', 0)} | {state}"
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


def _fit(head, blocks, tail, limit=PROMPT_CHARS):
    """Join head, (header, body) blocks and tail; trim the longest bodies until the whole fits in limit."""
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
    return "\n\n".join([head] + [f"{h}\n\n{b}" for h, b in blocks] + [tail])


def build_prompt(root, now, secrets=(), listed=None):
    """The auditor prompt: job text, the data blocks, the answer schema; masked, at most PROMPT_CHARS."""
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
        ("## 실험 기록 (커널 판정)", exp),
        ("## 최근 점검 (운영자)", "\n".join(_wake_text(w) for w in wakes) or "없음"),
        ("## 운영자 메모 (앞 800자)", _notes(root) or "없음"),
        ("## 지금 안내 (guidance)", guidance(root) or "없음"),
        ("## 안내 이력 (최근 4판, 커널이 잰 전후 점수)", _history_block(st["history"])),
    ]
    blocks = [(h, c.mask(b, secrets)) for h, b in blocks]
    return _fit(c.mask(job, secrets), blocks, "## 답 형식\n\n" + SCHEMA)


# ------------------------------------------------------------ apply
def tells_value(text):
    """True when text attaches a number to a policy key name (within 25 characters on the same line)."""
    return bool(VALUE_RX.search(text))


def _apply_guidance(root, now, text, secrets, st):
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
    cur = guidance(root)
    if text.strip() == cur.strip():
        return {"changed": False, "unchanged": True}
    hist = st["history"]
    prev_ts = (
        hist[-1].get("ts") if hist and _num(hist[-1].get("ts")) else now - 24 * 3600
    )
    before, _ = _score(root, now, prev_ts)
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
    st["history"] = hist[-HISTORY_MAX:]
    _atomic_write(_path(root, GUIDANCE), text)
    return {"changed": True, "id": nid, "chars": len(text)}


def _names(v):
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _apply_tools(root, now, ask, listed, secrets, traj):
    applied, refused = [], []
    if not isinstance(ask, dict):
        return {"applied": applied, "refused": refused}
    ts = tool_state(root)
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
    if applied:
        _atomic_json(_path(root, TOOL_STATE), {"hidden": hidden, "desc": desc})
    return {"applied": applied, "refused": refused}


# ------------------------------------------------------------ run
def _finish(root, traj, now, st, out):
    st["last_run_ts"] = now
    st["verdicts_seen"] = len(_ended(root))
    _atomic_json(_path(root, STATE), st)
    traj.append("auditor", "auditor_run", **out)
    return out


def run(
    root, terra, traj, now, secrets=(), listed=None, effort="medium", timeout_s=300
):
    """One audit: judge the active guidance, one model call, apply guarded changes. -> the auditor_run row fields."""
    reason = due(root, now) or "수동 실행"
    judged = judge_guidance(root, now, traj)
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
    if judged is not None:
        out["judged"] = {"id": judged.get("id"), "status": judged.get("status")}
    if _num(st.get("last_run_ts")) and not _compact_wakes(root, st["last_run_ts"]):
        return _finish(
            root,
            traj,
            now,
            st,
            dict(out, status="skipped", note="지난 감사 뒤 운영자 점검 없음"),
        )
    listed = list(listed) if listed is not None else _default_listed(root)
    prompt = build_prompt(root, now, secrets, listed)
    out["prompt_chars"] = len(prompt)
    try:
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
    g = _apply_guidance(root, now, ans.get("guidance"), secrets, st)
    out["guidance_changed"] = g["changed"]
    out["guidance"] = g
    out["tools"] = _apply_tools(root, now, ans.get("tools"), listed, secrets, traj)
    return _finish(root, traj, now, st, dict(out, status="done"))


_RETRY_AT = {}  # root -> earliest next try after a crash (the state file may be the thing that is broken)


def maybe_run(root, terra, traj, now, secrets=(), listed=None, force=False):
    """run() when due() or force, else None. Never raises: a crash is returned and logged as an auditor_run row,
    and the next try waits MIN_GAP_S so a broken state file cannot call the model on every poll."""
    try:
        if not force and (now < _RETRY_AT.get(root, 0) or not due(root, now)):
            return None
        return run(root, terra, traj, now, secrets=secrets, listed=listed)
    except Exception as e:  # noqa: BLE001 - the auditor must never stop the operator daemon
        _RETRY_AT[root] = now + MIN_GAP_S
        out = {"status": "crash", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        try:
            traj.append("auditor", "auditor_run", **out)
        except Exception:  # noqa: BLE001
            pass
        return out
