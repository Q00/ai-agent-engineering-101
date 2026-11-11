"""Operator: wake conditions, the tool loop with Terra and the effects of its done action (human code).

    python3 -m kernel.operator --root R [--once] [--dry] [--harness DIR] [--cli PATH] [--role shop|buyer]

The kernel only bounds the operator's own settings, runs tools and applies edits through kernel.versions. What to
change and why is the model's (harness/operator text). Each wake: one prompt per step with the operator's always-read
harness, MARKET_BRIEF.md, the briefing (version results and call refusals near the top), the wake reason, the tool
list and the action schema; the model answers one JSON action per step. --dry swaps the model for a stub that
answers {"done": {}} at once.

--role (env A2_OPERATOR_ROLE) runs one of two role operators in compact mode: shop (sale, restock and shop values, the
seller and restock rule code) or buyer (buyer.* values, the buyer rule code). Each has its own job text, notes, state,
guidance and wake rule and may change only its own side; the toolbox, ledgers, policy.json, memory, auditor and
trajectory are shared, and the two never wake at the same time (a2/operator_busy.json). No role: the single operator.
"""

import argparse
import contextlib
import fcntl
import importlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

from kernel import brief as b
from kernel import common as c
from kernel import experiments as ex
from kernel import harness as hz
from kernel import audit, auditor, code_edit, made_tools, policy_edit, questions, tools, versions
from kernel.agent import ParseError, parse_last_object
from kernel.llm import ModelError, ModelRefused
from kernel.trajectory import Trajectory

RESTOCK_WAKE_S = 600  # a missing or expired restock plan wakes the operator at most this often

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(HERE, "harness")
MARKET_BRIEF = os.path.join(HERE, "MARKET_BRIEF.md")
CLI = "/home/sungsoo/.local/bin/codex"  # 11-07 오후: codex 한도 초기화로 gpt-6.1-terra 복귀 (kernel/llm.py)
POLL_S = 60
BRIEF_CHARS = 24_000  # about 6k tokens (DESIGN 8, estimate)
OBS_TOTAL_CHARS = 120_000  # about 30k tokens of observations per wake (DESIGN 8, estimate)
EDIT_TARGETS = ("seller", "buyer", "operator")
BOUNDS = {
    "interval_s": (300, 7200, 1800),
    "wake_after_requests": (30, 5000, 150),
    "max_steps": (1, 30, 12),
    "breakage_n": (versions.BREAKAGE_BOUNDS[0], versions.BREAKAGE_BOUNDS[1], versions.BREAKAGE_DEFAULT),
    "rank_jump": (1, 50, 3),
    "bankrupt_near_factor": (1.0, 20.0, 2.0),
}
# Compact mode (DESIGN 14.2, A2_OPERATOR_COMPACT=1, only while LIVE is off): a small always-on prompt (job text, guide
# sections 1 and 7, state card, the operator's notes), details through tools, observations kept one step, every policy
# edit registered as an experiment the kernel judges and reverts (kernel.experiments)
JOB = os.path.join(HERE, "OPERATOR_JOB.md")
def _compact_steps():
    try:
        return max(3, min(12, int(os.environ.get("A2_COMPACT_STEPS") or 6)))
    except ValueError:
        return 6


# model calls per wake (env A2_COMPACT_STEPS, 3 to 12). 11-11: the shop operator used all 6 on reading code in 3 of 3
# wakes and never reached a change, so its launcher line asks for 10.
COMPACT_STEPS = _compact_steps()
COMPACT_TOOLS = 3  # tool calls per step
COMPACT_OBS = 6_000  # chars kept of one tool result
OBS_CAP = {"code_read": 10_000}  # 11-11: 30 of 36 code reads were shown cut at 6,000; code_read stops itself at 9,000


def obs_cap(name):
    """Chars of one result of tool name the compact operator is shown."""
    return OBS_CAP.get(name, COMPACT_OBS)

NOTES_CHARS = 2_000
# Heartbeat while an experiment runs. 1200 since 11-11 (성수님: 20분마다 시장을 보고 전략을 잡아야 함); it was 3600,
# and with experiments always running the operator woke hourly and mostly waited.
BUSY_INTERVAL_S = 1200
# Role operators (11-11): one shared toolbox, ledgers and auditor, two operators that change only their own side
ROLES = tools.ROLES
ROLE_KO = code_edit.ROLE_KO
ROLE_INTERVAL_S = 1200  # a role operator's interval without A2_OPERATOR_INTERVAL_S
ROLE_ONLY_TOOLS = ("tool_search",)  # not listed for the single operator
SHOP_LEAD_S = 300  # env A2_SHOP_LEAD_S: the shop wakes this long before the next restock window opens
WINDOW_AHEAD_S = 1800  # a window estimate further ahead than this, or already past, leaves it to the interval
SHOP_MIN_GAP_S = 600  # the window rule never wakes the shop more often than this
WINDOW_LOOKBACK_S = 6 * 3600  # restock rows read for the window estimate
WINDOW_BYTES = 5_000_000
BUSY = os.path.join("a2", "operator_busy.json")  # {role, pid, ts} while a role operator's wake runs
BUSY_STALE_S = 900
LEDGER_LOCK = os.path.join("a2", "operator_ledger.lock")  # role mode: the two processes' ledger writes take turns
AUDITOR_LOCK = os.path.join("a2", "auditor.lock")
OBS_BUDGET = 14_000  # chars of tool results shown per step: newest in full, older ones cut to OBS_TRIM
OBS_TRIM = 1_000
TOOL_ERR = ("도구 오류", "인자 오류", "도구 없음", "만든 도구 없음", "기억 없음", "operator는")  # how a failed tool answers


def obs_facts(obs, limit=None):
    """What the tool row keeps of an observation for the audit: its first line, whether the tool failed, and
    whether the operator saw it cut (longer than limit)."""
    head = " ".join(str(obs).split())[:120]
    out = {"obs_head": head, "err": True} if head.startswith(TOOL_ERR) else {"obs_head": head}
    if limit and len(str(obs)) > limit:
        out["cut"] = True
    return out


TEXT_TOOLS = ("brief_section", "guide", "recall", "code_read", "code_search")  # they return prose: a number match says nothing about use
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text):
    """Number tokens of a text, commas dropped; one-digit tokens are too common to mean anything."""
    out = set()
    for tok in _NUM.findall(str(text)):
        tok = tok.replace(",", "")
        if "." in tok:
            tok = tok.rstrip("0").rstrip(".")
        if len(tok.replace(".", "")) >= 2:
            out.add(tok)
    return out


def grounding(base, obs_all, done, kept=()):
    """Did the wake's reading reach its decision? Mechanical check on number tokens only.

    cited/uncited: a tool result counts as used when a number that only it showed (not on the state card) appears
    in the done object or in a kept conclusion. unfounded: numbers in a policy item's reason or expect found nowhere
    in what the operator was shown this wake (prompt, tool results), other than the item's own value.
    """
    shown = numbers(base)
    said = numbers(json.dumps(done, ensure_ascii=False, default=str) + " " + " ".join(kept))
    cited, uncited, seen = 0, [], set(shown)
    for head, (_, _, text) in obs_all.items():
        try:
            name = str(json.loads(head).get("tool"))
        except (ValueError, AttributeError):
            name = "?"
        own = numbers(text)
        seen |= own
        if (own - shown) & said:
            cited += 1
        elif not str(text).startswith(TOOL_ERR) and name not in TEXT_TOOLS:
            uncited.append(name)
    unfounded = []
    for it in done.get("policy") if isinstance(done.get("policy"), list) else []:
        if isinstance(it, dict):
            claim = numbers(f"{it.get('reason') or ''} {it.get('expect') or ''}") - numbers(it.get("value"))
            unfounded += sorted(claim - seen)
    return {"read": len(obs_all), "cited": cited, "uncited": uncited[:12], "unfounded": unfounded[:8]}


ASK_TIMEOUT_S = 120  # per model call; about twice the slowest answered call
REV_WEIGHT_MAX = 0.2
COMPACT_SCHEMA = """한 단계에 JSON 객체 하나로만 답함. 둘 중 하나:
1. 도구 호출 (한 번에 3개까지): {"tools": [{"tool": "<도구 이름>", "args": {<인자>}}], "keep": "<바로 앞 관찰에서 남길 결론, 300자 안>"}
2. 끝내기: {"done": {
  "policy": [{"section": "defaults.food|defaults.goods|<상품군 전체 경로>|shop|buyer.defaults.food|buyer.defaults.goods", "key": "<키>", "value": <수>, "reason": "<근거 숫자>", "expect": "<기대 효과: 어떤 숫자가 어느 쪽으로>", "window_h": <2~8, 생략 가능>}],
  "forum": [{"post": "<글>", "as": "shop|buyer"}],
  "notes": "<다음 점검의 네가 읽을 메모 전체, 2,000자 안. 생략하면 그대로 둠>",
  "new_tool": {"name": "q_<영소문자_숫자>", "description": "<어떤 숫자를 주는지>", "source": "events|metrics|buyer|trajectory", "hours": <1~48>, "where": [{"field": "<점 경로>", "op": "==|!=|>|>=|<|<=|in|contains|exists", "value": <값>}], "group_by": ["<점 경로>|hour|leaf"], "agg": [{"fn": "count|sum|mean|median|min|max|share", "field": "<점 경로>", "as": "<이름>"}], "limit": <줄 수>},
  "code": {"edits": [{"file": "<policy.py|hooks/restock.py|hooks/market_buyer.py|hooks/seller.py>", "old": "<바꿀 원문 그대로>", "new": "<새 글>"}], "reason": "<근거 숫자와 원인, 20자 이상>", "expect": "<기대 효과: 어떤 숫자가 어느 쪽으로, 20자 이상>"},
  "note": "<이번 점검 한 줄 요약>"}}
done의 모든 항목은 선택임. 지금 값과 허용 범위는 policy 도구로 봄.
code는 규칙 코드를 고침 (값으로 못 고치는 원인일 때만, 한 번에 하나): 고칠 파일은 이번 점검에서 code_read로 먼저 읽음. old는 파일에 정확히 한 번 나오는 원문, 수정 3개까지, 바뀐 줄 150줄까지.
커널이 규칙 코드의 시험을 돌려 새로 실패하는 시험이 있으면 거절함. 보호 함수(원가 이하 판매 금지, 정책 검증, 지출 상한, 서명, 주인 확인)를 바꾸거나 위험한 import, 호출, 파일 쓰기, URL을 새로 넣으면 거절함.
적용 뒤 커널이 30분 감시하고 (오류나 기록 끊김이면 바로 되돌림) 4시간 전후를 비교해 나빠지면 되돌림. policy.py와 hooks/seller.py 수정은 판매자 재시작 뒤 반영됨. 결과는 상태 카드의 [코드] 줄에 나옴.
new_tool은 읽기 전용 질의 도구를 하나 만듦 (한 점검에 하나, 모두 8개까지): 기록 파일 하나를 조건으로 거르고 묶어 세는 정의이고 코드는 아님. 커널이 실제 기록으로 시험 실행해 통과하면 다음 점검부터 도구 목록에 나옴. 기록의 필드 이름은 events, trajectory 도구로 행을 먼저 보고 정함. 있는 도구로 못 얻는 숫자가 반복해 필요할 때만 만듦.
policy 항목은 모두 실험으로 걸림: 판매 쪽 값은 동시 비교(물건 절반에만 새 값, 판정 때 채택 또는 버림), 구매와 재고 값은 전후 비교(바로 적용, 나빠지면 되돌림).
policy 키는 항목마다 숫자 하나: 판매 구역은 price_mult, min_margin, concession, max_rounds, stock_target, qty_per_buy, warranty_uplift, fresh_discount.B|C. goods 구역은 warranty_hours.A|B|C도. shop은 grade_floor_list.A|B|C와 c_first_leaves.<잎 이름> (1 넣기, 0 빼기). shop은 재입고와 재고 한도 값도 받음 (max_inventory_cost_frac, max_spend_per_run, max_lines_per_run, max_total_units, est_cost_frac 등. 전체 목록과 범위는 policy 도구). buyer.defaults.*는 max_pay_frac, open_frac (첫 제시가), step_frac (회차마다 올리는 폭), accept_c.max_pay_frac, accept_c.open_frac. bounds는 사람만 고침."""
RESERVED_ARGS = ("harness_root", "dry", "now")
HARNESS_TOOLS = ("versions", "harness_read", "replay_probe")

ACTION_SCHEMA = """한 단계에 JSON 객체 하나로만 답함. 둘 중 하나:
1. 도구 호출: {"tool": "<도구 이름>", "args": {<인자>}}
2. 끝내기: {"done": {
  "reflection": [{"target": "seller|buyer|operator", "version": <판 번호>, "effects": [{"edit": "<수정 요약>", "effect": "도움|해로움|효과 없음|판단 불가", "evidence": "<근거>"}], "lesson": "<교훈 하나 또는 null>"}],
  "edits": [{"target": "seller|buyer|operator", "files": {"<상대 경로>": "<파일 전체 내용 또는 null(삭제)>"}, "reason": "<근거: 궤적의 어느 순간, 어떤 숫자>"}],
  "revert": [{"target": "seller|buyer|operator", "version": <돌아갈 판>, "reason": "<이유>"}],
  "trial": {"target": "seller", "files": {"<상대 경로>": "<파일 전체 내용 또는 null>"}, "share": <후보로 보낼 새 요청 비율 0.1~0.9>, "reason": "<근거>"},
  "trial_end": {"target": "seller", "adopt": <true면 후보를 지금 판으로 설치, false면 버림>, "reason": "<근거>"},
  "settings": {"interval_s": <초>, "wake_after_requests": <건수>, ...},
  "restock_plan": {"buy": [{"sku": "<SKU>", "qty": <양의 정수>}], "valid_until": <유닉스 시각>},
  "policy": [{"section": "defaults.food|defaults.goods|<상품군 전체 경로>|shop|buyer.defaults.food|buyer.defaults.goods", "key": "<policy 도구의 편집 가능 키>", "value": <수>, "reason": "<근거: 어떤 숫자>"}],
  "forum": [{"post": "<글>", "as": "shop|buyer"}],
  "memcheck": <true|false>,
  "note": "<이번 점검 요약>"}}
done의 모든 항목은 선택임.
policy 키는 항목마다 숫자 하나: 판매 구역은 price_mult 등 7개, fresh_discount.B|C (0~0.5, 폭 0.06)도, goods 구역은 warranty_hours.A|B|C (정수 0~max_warranty_hours, 폭 6)도. shop은 grade_floor_list.A|B|C (0.3~0.9, 폭 0.05)와 c_first_leaves.<잎 이름> (1 넣기, 0 빼기). buyer.defaults.*는 max_pay_frac (0.5~1.0, 폭 0.05)와 accept_c.max_pay_frac (C등급 구매 상한, 0.4~1.0, 폭 0.05). bounds는 사람만 고침.
trial은 판매자 후보 판을 지금 판과 같은 시간에 나란히 돌림: 새 판매 요청이 task id 해시로 두 판에 무작위로 나뉨. 진행 중에는 판매자 edits와 revert가 막히고, trial_end로 채택하거나 버림. 브리핑의 [동시 비교] 줄에 두 판 숫자가 나옴."""


def bounded(settings):
    out = {}
    for k, (lo, hi, default) in BOUNDS.items():
        v = settings.get(k) if isinstance(settings, dict) else None
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            v = default
        v = max(lo, min(hi, v))
        out[k] = int(v) if isinstance(lo, int) else float(v)
    return out


def role_schema(role):
    """COMPACT_SCHEMA for a role operator: only the policy sections and keys, rule-code files and forum author that
    role may change, and a last line naming the other role."""
    other = "buyer" if role == "shop" else "shop"
    head, keys = COMPACT_SCHEMA.rsplit("\n", 1)
    i, j, k = keys.index("판매 구역은"), keys.index(" buyer.defaults.*는"), keys.index(" bounds는")
    keys = keys[:j] + keys[k:] if role == "shop" else keys[:i] + keys[j + 1:]
    sections = "defaults.food|defaults.goods|<상품군 전체 경로>|shop" if role == "shop" else "buyer.defaults.food|buyer.defaults.goods"
    for old, new in (
        ('"section": "defaults.food|defaults.goods|<상품군 전체 경로>|shop|buyer.defaults.food|buyer.defaults.goods"',
         f'"section": "{sections}"'),
        ('"as": "shop|buyer"', f'"as": "{role}"'),
        ("<policy.py|hooks/restock.py|hooks/market_buyer.py|hooks/seller.py>", "<" + "|".join(code_edit.ROLE_FILES[role]) + ">"),
        ('  "note": "<이번 점검 한 줄 요약>"}}', '  "ask": "<조사원에게 맡길 질문 하나, 400자 안>",\n  "note": "<이번 점검 한 줄 요약>"}}'),
    ):
        head = head.replace(old, new)
    head += ("\nask는 조사원에게 질문 하나를 맡김 (한 점검에 하나, 열린 질문은 운영자마다 2개까지): 원인을 확인하는 데 도구 호출이 "
             "3번 넘게 들 것 같을 때 씀. 조사원이 여러 단계로 확인해 다음 점검의 조사 결과 절에 답하고, 그동안 너는 아는 것으로 "
             "결정을 이어 감")
    return (head + "\n" + keys + f"\n너는 {ROLE_KO[role]}임. 위 구역, 파일, 글쓴이 밖은 {ROLE_KO[other]} 몫이라 커널이 거절함. "
            "도구는 둘이 같이 씀")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _openings(root, now):
    """Restock window openings recorded in the last WINDOW_LOOKBACK_S, sorted: ts + opens_in_s of attempt 1's restock
    hook rows in .r3/metrics.jsonl (outcome.why window_closed) and of the kernel hook's trajectory rows (kind restock)."""
    out = []
    for path, pick in (
        (os.path.join(root, ".r3/metrics.jsonl"),
         lambda m: m.get("outcome") if m.get("hook") == "restock" and isinstance(m.get("outcome"), dict) else None),
        (os.path.join(root, "a2/trajectory.jsonl"), lambda r: r if r.get("kind") == "restock" else None),
    ):
        for r in c.tail_json(path, WINDOW_BYTES, b'"opens_in_s"'):
            o, ts = pick(r), r.get("ts")
            wait = o.get("opens_in_s") if o else None
            if _num(ts) and _num(wait) and wait > 0 and now - WINDOW_LOOKBACK_S <= ts <= now:
                out.append(ts + wait)
    return sorted(out)


def next_window_ts(root, now):
    """Next restock window opening from recorded data, or None: the market's .market/inventory.json restock.opens_at
    when it is ahead; else the recorded openings (_openings, one per minute): the nearest one ahead, or the last one
    carried forward by the median gap between them."""
    inv = c.load_json(os.path.join(root, ".market/inventory.json"), {})
    w = inv.get("restock") if isinstance(inv, dict) and isinstance(inv.get("restock"), dict) else {}
    if _num(w.get("opens_at")) and w["opens_at"] > now:
        return float(w["opens_at"])
    seen = []
    for t in _openings(root, now):  # every hook run before a window reports it: openings within a minute are one
        if not seen or t - seen[-1] > 60:
            seen.append(t)
    ahead = [t for t in seen if t > now]
    if ahead:
        return ahead[0]
    if len(seen) < 2:
        return None
    gap = statistics.median(b - a for a, b in zip(seen, seen[1:]))
    if gap < SHOP_MIN_GAP_S:
        return None
    return seen[-1] + gap * (int((now - seen[-1]) // gap) + 1)


@contextlib.contextmanager
def _flock(root, rel, block=True):
    """Exclusive lock on <root>/<rel>; yields False when block is False and another process holds it."""
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError, OverflowError):
        return False
    return True


def busy_other(root, role, now):
    """The other role's busy lock {role, pid, ts} while its wake runs, else None. A lock older than BUSY_STALE_S or
    whose pid is dead is stale and ignored."""
    b = c.load_json(os.path.join(root, BUSY))
    if not isinstance(b, dict) or b.get("role") == role:
        return None
    pid = b.get("pid")
    if not _num(b.get("ts")) or now - b["ts"] > BUSY_STALE_S or not isinstance(pid, int) or not _alive(pid):
        return None
    return b


def claim_busy(root, role, now):
    """Take the busy lock for this role's wake. -> False while the other role's wake runs."""
    with _flock(root, BUSY + ".lock"):
        if busy_other(root, role, now):
            return False
        path = os.path.join(root, BUSY)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"role": role, "pid": os.getpid(), "ts": now}, f)
        os.replace(path + ".tmp", path)
    return True


def release_busy(root, role):
    """Drop the busy lock when this process holds it."""
    with _flock(root, BUSY + ".lock"):
        b = c.load_json(os.path.join(root, BUSY))
        if isinstance(b, dict) and b.get("role") == role and b.get("pid") == os.getpid():
            os.unlink(os.path.join(root, BUSY))


class DryTerra:
    """Stand-in model for --dry: ends every wake at once with no edits."""

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        return json.dumps({"done": {"edits": [], "reflection": [], "note": "dry"}})


def done_lines(done, result, now):
    """What the kernel did with a compact done, one line per submitted item, for the next wake's prompt."""
    out = [f"{c.kst(now, '%H:%M')} 점검"]
    items = done.get("policy") if isinstance(done.get("policy"), list) else []
    for it, r in zip(items, result.get("policy") or []):
        it, r = (it if isinstance(it, dict) else {}), (r if isinstance(r, dict) else {})
        head = f"- 값 {it.get('section')} {it.get('key')} → {it.get('value')}: "
        if r.get("ok"):
            out.append(head + f"적용됨 (실험 #{r.get('experiment')}, 판정 {r.get('eval_kst') or '-'})")
        else:
            out.append(head + f"거절됨. {str(r.get('error'))[:300]}")
    r = result.get("code")
    if isinstance(r, dict):
        out.append(f"- 코드 수정: " + (f"적용됨 (#{r.get('id')}, {r.get('status')})" if r.get("ok")
                                    else f"거절됨. {str(r.get('error'))[:300]}"))
    r = result.get("forum")
    if isinstance(r, dict):
        out.append(f"- 포럼 글: 대기열에 {r.get('queued', 0)}개" + (f", 거절: {r['refused']}" if r.get("refused") else ""))
    r = result.get("ask")
    if isinstance(r, dict):
        out.append("- 조사 질문: " + (f"접수됨 (#{r.get('id')}, 답은 조사 결과 절에 나옴)" if r.get("ok")
                                    else f"거절됨. {str(r.get('error'))[:300]}"))
    r = result.get("new_tool")
    if isinstance(r, dict):
        out.append(f"- 도구 만들기 {r.get('name')}: " + ("만들어짐" if r.get("ok") else f"거절됨. {str(r.get('error'))[:300]}"))
    if len(out) == 1:
        out.append("- 낸 것 없음 (값, 코드, 글, 도구 모두)")
    return out


class Operator:
    role = None  # "shop" or "buyer" for a role operator, None for the single operator
    agent = "operator"  # the trajectory agent of this process's rows (kernel.analyst: "analyst")

    def __init__(self, root, terra, traj, harness_root=HARNESS, market_brief_path=MARKET_BRIEF, dry=False,
                 spawn=subprocess.Popen, role=None):
        self.root, self.terra, self.traj = root, terra, traj
        self.harness_root, self.market_brief_path, self.dry, self.spawn = (
            harness_root,
            market_brief_path,
            dry,
            spawn,
        )
        self.login = c.owner_login(root)
        self.secrets = (self.login,) if self.login else ()
        self.role = role
        self.state_path = os.path.join(root, "a2/operator_state.json" if role is None else f"a2/operator_state_{role}.json")
        self._seen_next = None  # role mode: the newest verdict time this wake answers (wake_reason sets it)
        if role is not None:
            self._seed_notes()

    # ------------------------------------------------------------ settings and state
    def settings(self):
        s = dict(c.load_json(os.path.join(self.harness_root, "operator", "settings.json"), {}) or {})
        env = os.environ.get("A2_OPERATOR_INTERVAL_S", "")  # 2026-11-10: the human's interval wins over the harness
        if env.isdigit():
            s["interval_s"] = int(env)
        elif self.role is not None:  # role operators wake on a fixed interval, not the harness's
            s["interval_s"] = ROLE_INTERVAL_S
        return bounded(s)

    def _state(self):
        st = c.load_json(self.state_path, {})
        return st if isinstance(st, dict) else {}

    def _save_state(self, st):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------ wake conditions
    def _rank(self):
        snaps = b.board_snapshots(self.root)
        if not snaps:
            return None
        P = (c.load_json(snaps[-1][1], {}) or {}).get("players") or []
        E = sorted(P, key=lambda p: -(p.get("total") or 0))
        return next((i + 1 for i, p in enumerate(E) if c.is_mine(p, self.login)), None)

    def _shop_missing(self):
        sh = c.load_json(os.path.join(self.root, ".market/shops.json"))
        rows = sh if isinstance(sh, list) else list(sh.values()) if isinstance(sh, dict) else []
        # live rows carry only name, offers, reviews, url (11-10): without an owner field nothing can be said
        if not any(isinstance(r, dict) and any(k in r for k in ("owner", "player", "github")) for r in rows):
            return False
        return not any(isinstance(r, dict) and c.is_mine(r, self.login) for r in rows)

    def _bankrupt_near(self, factor):
        inv = c.load_json(os.path.join(self.root, ".market/inventory.json"), {}) or {}
        bal, line = inv.get("balance"), inv.get("bankrupt_line")
        if not isinstance(bal, (int, float)) or not isinstance(line, (int, float)):
            return False
        return bal <= (line * factor if line > 0 else line)

    def _last_edit_ts(self, target):
        ts = 0.0
        for r in c.tail_json(os.path.join(self.root, "a2/trajectory.jsonl"), 20_000_000, b'"harness_'):
            if r.get("kind") in ("harness_edit", "harness_revert") and r.get("target") == target:
                ts = max(ts, r.get("ts") or 0)
        return ts

    def seller_requests_since(self, ts):
        return sum(
            1
            for r in c.tail_json(os.path.join(self.root, "a2/trajectory.jsonl"), 20_000_000, b'"seller.open"')
            if r.get("kind") == "decision"
            and r.get("agent") == "seller"
            and r.get("decision_kind") == "seller.open"
            and (r.get("ts") or 0) > ts
        )

    def events_now(self, s):
        return {
            "rank": self._rank(),
            "shop_missing": self._shop_missing(),
            "bankrupt_near": self._bankrupt_near(s["bankrupt_near_factor"]),
        }

    def wake_reason(self, now=None):
        """Why the operator should wake now, or None. Flags that cleared are saved so the next onset wakes again."""
        now = c.now_ts() if now is None else now
        if self.compact():
            return self._wake_reason_compact(now)
        if self.role is not None:  # role operators work in compact mode only
            return None
        s, st = self.settings(), self._state()
        ev = self.events_now(s)
        reasons = []
        last = st.get("last_wake_ts")
        if last is None or now - last >= s["interval_s"]:
            reasons.append("주기 점검")
        n = self.seller_requests_since(max(last or 0, self._last_edit_ts("seller")))
        if n >= s["wake_after_requests"]:
            reasons.append(f"마지막 수정 또는 점검 뒤 판매 요청 {n}건")
        if ev["rank"] and st.get("last_rank") and abs(ev["rank"] - st["last_rank"]) >= s["rank_jump"]:
            reasons.append(f"Everyone 순위 {st['last_rank']}위에서 {ev['rank']}위로 바뀜")
        if self.live() and b.restock_plan_state(self.root, now)[0] != "valid" and (
            last is None or now - last >= RESTOCK_WAKE_S
        ):
            reasons.append("재입고 계획 없음 또는 만료")
        if ev["shop_missing"] and not st.get("shop_missing"):
            reasons.append("우리 가게가 가게 목록에서 빠짐")
        if ev["bankrupt_near"] and not st.get("bankrupt_near"):
            reasons.append("잔고가 파산선 가까이 내려감")
        cleared = [k for k in ("shop_missing", "bankrupt_near") if st.get(k) and not ev[k]]
        if cleared:
            for k in cleared:
                st[k] = False
            self._save_state(st)
        return ", ".join(reasons) or None

    def live(self):
        """True while <root>/a2/LIVE exists: attempt 2 decides sales; otherwise attempt 1's rules do."""
        return os.path.exists(os.path.join(self.root, "a2", "LIVE"))

    def _mode_text(self):
        if self.live():
            return "LIVE 켜짐: 시도 2의 판매자, 구매자, 재입고 계획이 가게를 운영함. done.policy는 쓰지 않음"
        return (
            "LIVE 꺼짐 (11-07 11시 성수님 결정, Codex 구독 한도 소진): 판매, 구매, 재입고는 시도 1의 규칙 코드가 policy.json 값을 읽어 "
            "결정함. 재입고 물량은 hooks/restock.py가 policy.py의 restock_plan 함수를 불러 정하고, 이 함수는 지금 실제로 돌고 있음. "
            "받지 않는 것은 시도 2 방식의 출력 항목뿐임: 판매자와 구매자 지침 수정(edits의 seller, buyer), trial, done의 restock_plan 항목. "
            "네가 규칙을 바꾸는 길은 둘임: 값은 done.policy(policy 도구로 지금 값을 보고, 키 하나씩 허용 폭 안에서, 근거 숫자를 reason에), "
            "규칙의 동작은 done.code(일 문서의 규칙 코드 고치기). 고친 뒤의 효과 판정은 실험 장부가 함. "
            "자기 지침(edits의 operator)과 메모는 지금처럼 고칠 수 있음"
        )

    # ------------------------------------------------------------ tool loop
    def _always_text(self):
        d = os.path.join(self.harness_root, "operator")
        errs = hz.validate_dir(d)
        if errs:
            return None, errs
        h = hz.load(d)
        try:
            with open(os.path.join(d, "settings.json"), encoding="utf-8") as f:
                settings_text = f.read()
        except OSError:
            settings_text = "{}"
        return (h, h.always_text + "\n## settings.json (지금 값, 커널이 범위로 묶음)\n\n" + settings_text), []

    def _market_brief(self):
        try:
            with open(self.market_brief_path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def _tool_list(self):
        return "\n".join(
            f"- {name}: {' '.join((fn.__doc__ or '').split())[:300]}"
            for name, (fn, agents) in tools.TOOLS.items()
            if "operator" in agents and name not in ROLE_ONLY_TOOLS
        )

    def listed(self):
        """Compact-mode tool list as (name, description): built-ins the auditor has not hidden (with its
        re-descriptions), then the operator-made query tools."""
        try:
            state = auditor.tool_state(self.root)
        except Exception:  # a broken auditor file must not take the tool list away
            state = {"hidden": [], "desc": {}}
        out = [
            (name, state["desc"].get(name) or " ".join((fn.__doc__ or "").split())[:160])
            for name, (fn, agents) in tools.TOOLS.items()
            if "operator" in agents and name not in HARNESS_TOOLS
            and (self.role is not None or name not in ROLE_ONLY_TOOLS)
            and (name not in state["hidden"] or name in auditor.CORE_TOOLS)
        ]
        try:
            out += [(n, "(만든 도구) " + str(d)[:160]) for n, d in made_tools.listing(self.root)]
        except Exception:
            pass
        return out

    def _recommended(self):
        try:
            return auditor.recommended(self.root, self.role)
        except Exception:  # a broken auditor file must not take the list away
            return list(tools.RECOMMENDED.get(self.role, ()))

    def _tool_text(self, listed):
        """The prompt's tool section: every listed tool with its description; for a role operator only its recommended
        tools, the others by name on one line."""
        if self.role is None:
            return "\n".join(f"- {name}: {doc}" for name, doc in listed)
        rec = set(self._recommended())
        out = [f"- {name}: {doc}" for name, doc in listed if name in rec]
        rest = [name for name, _ in listed if name not in rec]
        if rest:
            out.append("- 그 밖의 도구 (이름만. 설명과 인자는 tool_search로 보고, 그대로 부를 수 있음): " + ", ".join(rest))
        return "\n".join(out)

    def _tool_extra(self, name, args, obs):
        """Tool row fields of a role operator: whether the tool is on its recommended list; for tool_search the query
        and the number of tools found."""
        if self.role is None:
            return {}
        out = {"recommended": name in self._recommended()}
        if name == "tool_search":
            found = re.findall(r"': (\d+)개", str(obs).split("\n", 1)[0])
            out.update(query=c.mask(str((args or {}).get("query") or "")[:60], self.secrets),
                       hits=int(found[-1]) if found else None)
        return out

    def _run_tool(self, name, args):
        args = {k: v for k, v in (args if isinstance(args, dict) else {}).items() if k not in RESERVED_ARGS}
        if name in HARNESS_TOOLS and self.compact():  # not listed in compact mode, so not callable either
            return "도구 오류: 이 방식에서는 쓸 수 없는 도구"
        if isinstance(name, str) and name.startswith(made_tools.PREFIX) and self.compact():
            try:
                obs = made_tools.call(self.root, name, args or None)
            except Exception as e:
                obs = f"도구 오류: {type(e).__name__}: {e}"
            obs = c.cap(c.mask(str(obs), self.secrets), tools.MAX_CHARS)
            self.traj.append(self.agent, "tool", name=name, obs_chars=len(obs), **obs_facts(obs, COMPACT_OBS),
                             args=c.mask(json.dumps(args, ensure_ascii=False, default=str)[:2000], self.secrets),
                             **self._tool_extra(name, args, obs))
            return obs
        if name in HARNESS_TOOLS:
            args["harness_root"] = self.harness_root
        if name == "replay_probe":
            args["dry"] = self.dry
        try:
            obs = tools.call(name, "operator", self.root, **args)
        except Exception as e:  # a tool fed bad values by the model is an observation, not a crash
            obs = f"도구 오류: {type(e).__name__}: {e}"
        obs = c.cap(c.mask(str(obs), self.secrets), tools.MAX_CHARS)
        shown = {k: v for k, v in args.items() if k not in RESERVED_ARGS}
        self.traj.append(
            self.agent,
            "tool",
            name=name,
            args=c.mask(json.dumps(shown, ensure_ascii=False, default=str)[:2000], self.secrets),
            obs_chars=len(obs),
            **obs_facts(obs, obs_cap(name) if self.compact() else None),
            **self._tool_extra(name, shown, obs),
        )
        return obs

    def _effort(self, route):
        # A2_OPERATOR_EFFORT (11-10, 성수님 결정: 사용량 절감) overrides the harness route's effort when set
        return os.environ.get("A2_OPERATOR_EFFORT") or route.get("effort", "medium")

    def _ask(self, prompt, route):
        effort = self._effort(route)
        # A hung CLI call, not slow thinking, is what times out: 5 of 7,870 calls in 72 h ran the full 300 s while
        # the slowest answered one took 64 s. So wait ASK_TIMEOUT_S and ask once more instead of losing the wake.
        timeout_s = min(route.get("timeout_s", 300), ASK_TIMEOUT_S)
        try:
            return self.terra.complete(prompt, agent=self.agent, timeout_s=timeout_s, effort=effort)
        except ModelError as e:
            if str(e) != "timeout":
                raise
            return self.terra.complete(prompt, agent=self.agent, timeout_s=timeout_s, effort=effort)

    def wake(self, reason, now=None):
        """One wake: tool loop until done or max_steps. -> summary dict (also written as an operator_wake row)."""
        now = c.now_ts() if now is None else now
        if self.compact():
            return self._wake_compact(reason, now)
        s, st = self.settings(), self._state()
        ev = self.events_now(s)
        st.update(last_wake_ts=now, last_rank=ev["rank"], shop_missing=ev["shop_missing"],
                  bankrupt_near=ev["bankrupt_near"])
        last_brief = st.get("last_brief_ts")
        st["last_brief_ts"] = now
        self._save_state(st)
        for agent in EDIT_TARGETS:
            versions.ensure_baseline(self.harness_root, agent, now)
        self.trial_timeouts(now)

        loaded, errs = self._always_text()
        if loaded is None:
            return self._end(reason, 0, "operator_harness_invalid", {"errors": errs})
        h, always = loaded
        route = h.routing.get("operator.wake") or {}
        brief = c.cap(b.build(self.root, now, last_brief), BRIEF_CHARS)
        base = "\n\n".join(
            [
                always,
                self._market_brief(),
                "## 브리핑 (커널이 모은 사실, 해석 없음)",
                brief,
                "## 지금 운영 방식",
                self._mode_text(),
                "## 깨운 이유",
                reason,
                "## 도구 (운영자)",
                self._tool_list(),
                "## 답 형식",
                ACTION_SCHEMA,
            ]
        )
        history, used = [], 0
        for step in range(s["max_steps"]):
            last_step = step == s["max_steps"] - 1 or used >= OBS_TOTAL_CHARS
            prompt = base + "\n\n## 이번 점검의 도구 호출과 관찰\n\n" + ("\n\n".join(history) or "아직 없음")
            if last_step:
                prompt += "\n\n도구 단계나 관찰 한도를 다 씀. 이번 답은 반드시 done 객체여야 함."
            try:
                act = parse_last_object(self._ask(prompt, route))
            except (ModelRefused, ModelError) as e:
                return self._end(reason, step + 1, "model_error", {"error": f"{type(e).__name__}: {e}"})
            except ParseError:
                history.append(f"[단계 {step + 1}] 형식 오류: JSON 객체 하나로 답해야 함")
                continue
            if isinstance(act.get("done"), dict):
                result = self.finish(act["done"], prompt, route, now)
                return self._end(reason, step + 1, "done", result)
            if last_step:
                break
            name = act.get("tool")
            obs = self._run_tool(name, act.get("args"))
            used += len(obs)
            history.append(
                f"[단계 {step + 1}] {json.dumps({'tool': name, 'args': act.get('args')}, ensure_ascii=False)[:500]}\n{obs}"
            )
        return self._end(reason, step + 1, "no_done", {})

    def _end(self, reason, steps, status, result):
        self.traj.append("operator", "operator_wake", reason=reason, steps=steps, status=status, result=result)
        return dict(result, status=status, steps=steps)

    # ------------------------------------------------------------ compact mode
    def compact(self):
        return os.environ.get("A2_OPERATOR_COMPACT") == "1" and not self.live()

    def _cats(self):
        return {v.get("category") for v in b._catalog(self.root).values() if isinstance(v, dict) and v.get("category")}

    def _notes_path(self):
        return os.path.join(self.root, "a2/operator_notes.md" if self.role is None else f"a2/operator_notes_{self.role}.md")

    def _seed_notes(self):
        """First start of a role with no notes file: its notes start as a copy of the single operator's."""
        path, single = self._notes_path(), os.path.join(self.root, "a2/operator_notes.md")
        if not os.path.exists(path) and os.path.exists(single):
            shutil.copyfile(single, path)

    def _job_path(self):
        """OPERATOR_JOB_<ROLE>.md for a role operator when it is there, else OPERATOR_JOB.md."""
        if self.role is not None:
            p = os.path.join(HERE, f"OPERATOR_JOB_{self.role.upper()}.md")
            if os.path.exists(p):
                return p
        return JOB

    def _ledger_lock(self):
        return _flock(self.root, LEDGER_LOCK) if self.role is not None else contextlib.nullcontext()

    def _role_kw(self):
        return {"role": self.role} if self.role is not None else {}

    def _role_events(self, st, now):
        """This role's experiment and code-change verdicts since it last woke, read from the ledgers (a verdict the
        other process reached wakes this one too). The first call only marks the start."""
        seen = st.get("events_seen_ts")
        if not _num(seen):
            st["events_seen_ts"] = now
            self._save_state(st)
            return [], []
        exps = [e for e in ex.load(self.root) if isinstance(e, dict) and e.get("status") not in (None, "running")
                and _num(e.get("ended_ts")) and e["ended_ts"] > seen and ex.role_of(e) == self.role]
        codes = [r for r in code_edit.load(self.root) if r.get("status") not in code_edit.OPEN
                 and _num(r.get("ended_ts")) and r["ended_ts"] > seen and self.role in code_edit.role_of(r)]
        self._seen_next = max([seen] + [x["ended_ts"] for x in exps + codes])
        return exps, codes

    def _window_reason(self, now, last):
        """Shop: wake A2_SHOP_LEAD_S (SHOP_LEAD_S) before the next restock window opens, at most every SHOP_MIN_GAP_S
        and once per window. -> the reason, "" while it is not time, None when there is no usable estimate (none,
        past or more than WINDOW_AHEAD_S away): the interval decides then."""
        nxt = next_window_ts(self.root, now)
        if nxt is None or not now < nxt <= now + WINDOW_AHEAD_S:
            return None
        lead = os.environ.get("A2_SHOP_LEAD_S", "")
        at = nxt - (int(lead) if lead.isdigit() else SHOP_LEAD_S)
        if now < at or (last is not None and (now - last < SHOP_MIN_GAP_S or last >= at)):
            return ""
        return f"재입고 창 {c.kst(nxt, '%H:%M')} 열리기 {max(0, round((nxt - now) / 60))}분 전"

    def _side_error(self, section):
        if self.role == "shop":
            return (f"{section[:60]}는 구매 쪽 값이라 {ROLE_KO['buyer']}가 고침. {ROLE_KO['shop']}는 buyer.로 시작하지 "
                    "않는 구역만 고침")
        return (f"{section[:60]}는 가게 쪽 값이라 {ROLE_KO['shop']}가 고침. {ROLE_KO['buyer']}는 buyer.defaults.food, "
                "buyer.defaults.goods만 고침")

    def _notes(self):
        try:
            with open(self._notes_path(), encoding="utf-8") as f:
                return f.read()[:NOTES_CHARS]
        except OSError:
            return ""

    def _rev_weight(self):
        """What one won of revenue is worth in net-profit won for the shop score, from the newest board: shop all =
        0.5 pct(book) + 0.3 pct(revenue) + ..., and a won moves a percentile by the density of shops around our
        value (shops within 25% of it). Capped at REV_WEIGHT_MAX; 0 when the board cannot say."""
        snaps = b.board_snapshots(self.root)
        if not snaps:
            return 0.0
        snap = c.load_json(snaps[-1][1], {})
        rows = snap.get("shops") if isinstance(snap, dict) else None
        shops = [r for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict)]
        me = next((r for r in shops if c.is_mine(r, self.login)), None)
        if me is None:
            return 0.0

        def num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        def density(k):
            v = me.get(k)
            if not num(v) or v <= 0:
                return None
            return sum(1 for r in shops if r is not me and num(r.get(k)) and abs(r[k] - v) <= 0.25 * v) / (0.5 * v)

        db, dr = density("book"), density("revenue")
        if not db or not dr:
            return 0.0
        return round(min(REV_WEIGHT_MAX, 0.3 * dr / (0.5 * db)), 4)

    def _wake_reason_compact(self, now):
        """Compact mode wakes on events: an experiment verdict, a rank jump or an alarm at once; otherwise every
        interval_s while nothing is being tested and every BUSY_INTERVAL_S while an experiment runs. The experiment
        ledger is ticked here on every poll (code only, no model call)."""
        s, st = self.settings(), self._state()
        ev = self.events_now(s)
        with self._ledger_lock():
            ticked = ex.tick(self.root, now, self.traj, categories=self._cats())
        ended = [e for e in ticked if e.get("status") != "running"]
        reasons = []
        if self.role is not None:
            ended, codes = self._role_events(st, now)
            if codes:
                reasons.append(f"코드 수정 판정 {len(codes)}건 (상태 카드의 [코드] 줄)")
        if ended:
            reasons.append(f"실험 판정 {len(ended)}건 (상태 카드의 [실험] 줄)")
        last = st.get("last_wake_ts")
        gap = max(s["interval_s"], BUSY_INTERVAL_S) if ex.running(self.root) else s["interval_s"]
        window = self._window_reason(now, last) if self.role == "shop" else None
        if window is not None:
            if window:
                reasons.append(window)
        elif last is None or now - last >= gap:
            reasons.append("주기 점검")
        if ev["rank"] and st.get("last_rank") and abs(ev["rank"] - st["last_rank"]) >= s["rank_jump"]:
            reasons.append(f"Everyone 순위 {st['last_rank']}위에서 {ev['rank']}위로 바뀜")
        if self.role != "buyer" and ev["shop_missing"] and not st.get("shop_missing"):
            reasons.append("우리 가게가 가게 목록에서 빠짐")
        if ev["bankrupt_near"] and not st.get("bankrupt_near"):
            reasons.append("잔고가 파산선 가까이 내려감")
        cleared = [k for k in ("shop_missing", "bankrupt_near") if st.get(k) and not ev[k]]
        if cleared:
            for k in cleared:
                st[k] = False
            self._save_state(st)
        return ", ".join(reasons) or None

    def _wake_compact(self, reason, now):
        s, st = self.settings(), self._state()
        ev = self.events_now(s)
        st.update(last_wake_ts=now, last_rank=ev["rank"], shop_missing=ev["shop_missing"],
                  bankrupt_near=ev["bankrupt_near"])
        if self.role is not None and _num(self._seen_next):
            st["events_seen_ts"] = max(self._seen_next, st.get("events_seen_ts") or 0)
        last_brief = st.get("last_brief_ts")
        st["last_brief_ts"] = now
        self._save_state(st)
        loaded, _ = self._always_text()  # the harness only routes the call here (effort, timeout); JOB is the job text
        route = (loaded[0].routing.get("operator.wake") or {}) if loaded else {}
        try:
            with open(self._job_path(), encoding="utf-8") as f:
                job = f.read()
        except OSError:
            job = ""
        listed = self.listed()
        try:
            names = self._recommended() if self.role is not None else [n for n, _ in listed]
            audit_lines = audit.lines(self.root, now, listed=names, **self._role_kw())
        except Exception:  # the audit is a reader of the trajectory; it must never stop a wake
            audit_lines = []
        try:
            guidance = auditor.guidance(self.root, **self._role_kw())
        except Exception:
            guidance = ""
        code_lines = code_edit.lines(self.root, now, **self._role_kw())  # never raises
        base = "\n\n".join(
            [
                job,
                "## 감사자가 준 일하는 방식 (네 지난 점검들을 감사해 쓴 것. 전략 값이 아니라 일하는 방법임)\n\n"
                + (c.mask(guidance, self.secrets) or "없음"),
                tools.guide_always(),
                "## 상태 카드 (커널이 모은 사실, 해석 없음)",
                b.build_compact(self.root, now, last_brief,
                                extra=list(ex.lines(self.root, now, **self._role_kw())) + list(code_lines) + list(audit_lines)),
                "## 지난 점검에 네가 낸 것의 결과 (커널 기록. 거절된 것은 적용되지 않았음)",
                "\n".join(str(x) for x in (st.get("last_done") or {}).get("lines") or []) or "없음",
                *self._findings_block(now),
                "## 네 메모 (지난 점검의 네가 남김)",
                c.mask(self._notes(), self.secrets) or "없음",
                "## 깨운 이유",
                reason,
                "## 도구",
                self._tool_text(listed),
                "## 답 형식",
                COMPACT_SCHEMA if self.role is None else role_schema(self.role),
            ]
        )
        return self._compact_loop(base, route, reason, now)

    def _steps(self):
        return COMPACT_STEPS

    def _findings_block(self, now):
        """The prompt section of the investigator's findings for this role operator, [] when there are none."""
        if self.role not in ROLES:
            return []
        try:
            lines = questions.prompt_lines(self.root, self.role, now)
        except Exception:  # a reader of the findings file; it must never cost a wake
            lines = []
        return ["## 조사 결과 (조사원이 확인한 것. 결정은 네 몫)", c.mask("\n".join(lines), self.secrets)] if lines else []

    def _compact_loop(self, base, route, reason, now):
        """The compact step loop over the fixed prompt base: tool steps with kept conclusions until a done object or
        _steps() model calls."""
        # 11-11: results used to vanish after one step and 24 of 31 repeated calls re-read one two steps later.
        kept, fresh, chars, calls, step = [], [], 0, 0, 0
        self._kept, steps = kept, self._steps()  # kept: the conclusions so far (kernel.analyst reads them on no done)
        read_files = set()  # rule-code files read this wake: code_read, or code_search with a file (search hits alone do not count)
        obs_all, full = {}, set()  # (tool, args) -> [step, head, text]; keys shown in full in the last prompt
        for step in range(steps):
            last_step = step == steps - 1
            shown, left, full = [], OBS_BUDGET, set()
            for key, (at, head, text) in sorted(obs_all.items(), key=lambda kv: -kv[1][0]):
                if len(text) <= left:
                    full.add(key)
                else:
                    text = text[:OBS_TRIM] + "\n[오래된 관찰이라 앞부분만 남김. 더 필요하면 다시 부름]"
                left -= len(text)
                shown.append(f"[단계 {at + 1}] {head}\n{text}")
            prompt = (
                base
                + "\n\n## 남긴 결론\n\n"
                + ("\n".join(kept) or "없음")
                + "\n\n## 이번 점검의 관찰 (새것부터. 여기 있는 것은 다시 부르지 않음)\n\n"
                + ("\n\n".join(shown) or "없음")
                + ("" if obs_all or not fresh else "\n\n" + "\n".join(fresh))
            )
            if last_step:
                prompt += "\n\n도구 단계를 다 씀. 이번 답은 반드시 done 객체여야 함."
            chars += len(prompt)
            calls += 1
            try:
                act = parse_last_object(self._ask(prompt, route))
            except (ModelRefused, ModelError) as e:
                return self._end(reason, step + 1, "model_error",
                                 {"error": f"{type(e).__name__}: {e}", "prompt_chars": chars, "calls": calls})
            except ParseError:
                kept.append(f"[단계 {step + 1}] 형식 오류: JSON 객체 하나로 답해야 함")
                continue
            if fresh:
                keep = act.get("keep")
                kept.append(
                    f"[단계 {step}] "
                    + (c.mask(keep, self.secrets)[:400] if isinstance(keep, str) and keep.strip()
                       else "(결론을 남기지 않음) " + " / ".join(" ".join(x.split())[:150] for x in fresh))
                )
            if isinstance(act.get("done"), dict):
                result = self._finish_compact(act["done"], now, read_files)
                result.update(prompt_chars=chars, calls=calls)
                try:  # 11-11: the buyer operator sent one refused change in 3 wakes; it never saw the refusal
                    st2 = self._state()
                    st2["last_done"] = {"ts": now, "lines": done_lines(act["done"], result, now)}
                    self._save_state(st2)
                except Exception:  # a record for the next wake; it must never cost this one
                    pass
                try:
                    result["grounding"] = grounding(base, obs_all, act["done"], kept)
                except Exception:  # the check reads model text; it must never cost the wake
                    pass
                return self._end(reason, step + 1, "done", result)
            if last_step:
                break
            asked = act.get("tools") if isinstance(act.get("tools"), list) else [act] if act.get("tool") else []
            fresh = []
            for t in asked[:COMPACT_TOOLS]:
                if not isinstance(t, dict):
                    continue
                head = json.dumps({"tool": t.get("tool"), "args": t.get("args")}, ensure_ascii=False, default=str,
                                  sort_keys=True)[:300]
                if head in full:  # still on the page in full: no second read
                    self.traj.append(self.agent, "tool", name=t.get("tool"), obs_chars=0, cached=True,
                                     args=c.mask(json.dumps(t.get("args"), ensure_ascii=False, default=str)[:2000],
                                                 self.secrets))
                    fresh.append(head + "\n이미 읽음: 위 관찰에 그대로 있음")
                    continue
                obs = c.cap(self._run_tool(t.get("tool"), t.get("args")), obs_cap(t.get("tool")))
                obs_all[head] = [step, head, obs]
                f = (t.get("args") or {}).get("file") if isinstance(t.get("args"), dict) else None
                if t.get("tool") in ("code_read", "code_search") and isinstance(f, str) and not obs.startswith(TOOL_ERR):
                    read_files.add(f)
                fresh.append(head + "\n" + obs)
            if not fresh:
                fresh = ["도구 호출이 없음: tools 목록이나 done 객체로 답해야 함"]
        return self._end(reason, step + 1, "no_done", {"prompt_chars": chars, "calls": calls})

    def _finish_compact(self, done, now, read_files=()):
        """Apply a compact done: each policy item that no running experiment blocks is written through policy_edit
        and registered as an experiment; a code change goes through code_edit.propose (read_files: the rule-code files
        read this wake); forum posts are queued; notes replace the operator's notes file. A role operator's items of
        the other side are refused, and its experiments and code changes keep its role."""
        with self._ledger_lock():
            return self._finish_compact_locked(done, now, read_files)

    def _finish_compact_locked(self, done, now, read_files):
        result = {"note": c.mask(str(done.get("note") or ""), self.secrets)[:500]}
        items = done.get("policy")
        if isinstance(items, list) and items:
            cats, out, n_ok, weight = self._cats(), [], 0, None
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    out.append({"ok": False, "error": "each policy item must be an object"})
                    continue
                sec = it.get("section")
                if self.role is not None and isinstance(sec, str) and sec.startswith("buyer.") != (self.role == "buyer"):
                    out.append({"ok": False, "error": self._side_error(sec)})
                    continue
                if n_ok >= policy_edit.MAX_PER_WAKE:
                    out.append({"ok": False, "error": f"한 점검에 {policy_edit.MAX_PER_WAKE}개까지"})
                    continue
                try:  # ex.start checks, writes and registers as one step; now + i keeps backup names apart
                    weight = self._rev_weight() if weight is None else weight
                    exp = c.mask(str(it.get("expect") or it.get("reason") or ""), self.secrets)[:200]
                    r = ex.start(self.root, dict(it, expect=exp), now + i, self.traj, rev_weight=weight,
                                 categories=cats, split_ok=os.environ.get("A2_SPLIT") == "1")
                except Exception as e:  # a value the edit code did not expect is a refusal, not a crashed wake
                    r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
                if not isinstance(r, dict):
                    r = {"ok": False, "error": "no result"}
                if r.get("ok"):
                    n_ok += 1
                    if self.role is not None:
                        ex.set_role(self.root, r.get("experiment"), self.role)
                    if isinstance(r.get("eval_ts"), (int, float)):
                        r = dict(r, eval_kst=c.kst(r["eval_ts"]))
                out.append(r)
            result["policy"] = out
        if isinstance(done.get("code"), dict):
            code = done["code"]
            try:
                weight = self._rev_weight()
            except Exception:  # the board read only weights the shop metric
                weight = 0.0
            r = code_edit.propose(self.root, code.get("edits"), code.get("reason"), code.get("expect"), now, self.traj,
                                  read_files=read_files, rev_weight=weight, **self._role_kw())
            result["code"] = {"ok": r.get("ok"), "id": r.get("id"), "status": r.get("status"),
                              "error": c.mask(str(r["error"]), self.secrets)[:300] if r.get("error") else None,
                              "tests": (r.get("tests") or {}).get("summary")}
        if "forum" in done:
            posts = done["forum"]
            other = [p for p in posts if isinstance(p, dict) and p.get("as") in ROLES and p.get("as") != self.role] if (
                self.role is not None and isinstance(posts, list)) else []
            result["forum"] = self._forum([p for p in posts if not any(p is x for x in other)] if other else posts)
            if other:
                o = other[0]["as"]
                result["forum"]["refused"] = f"as {o} 글 {len(other)}개: {ROLE_KO[o]}가 씀. {ROLE_KO[self.role]}는 as {self.role}로만 씀"
        if isinstance(done.get("new_tool"), dict):  # declarative query only; the test run on real data is the gate
            try:
                r = made_tools.add(self.root, done["new_tool"], now, traj=self.traj, by=self.agent,
                                   reserved=tuple(tools.TOOLS), **self._role_kw())
            except Exception as e:
                r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            r = r if isinstance(r, dict) else {"ok": False, "error": "no result"}
            result["new_tool"] = {k: c.mask(str(v), self.secrets)[:300] if isinstance(v, str) else v
                                  for k, v in r.items() if k in ("ok", "name", "error", "sample")}
        if self.role in ROLES and "ask" in done:  # a question for the investigator (kernel.analyst)
            try:
                result["ask"] = questions.ask(self.root, self.role, done["ask"], now, self.secrets)
            except Exception as e:  # the queue file is shared; a failed write is a refusal, not a crashed wake
                result["ask"] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        if isinstance(done.get("notes"), str) and done["notes"].strip():
            path = self._notes_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.write(c.mask(done["notes"], self.secrets)[:NOTES_CHARS])
            os.replace(path + ".tmp", path)
            result["notes_chars"] = min(len(done["notes"]), NOTES_CHARS)
        return result

    # ------------------------------------------------------------ done
    def _apply_edits(self, edits, now):
        applied, errors = [], []
        for e in edits if isinstance(edits, list) else []:
            if not isinstance(e, dict) or e.get("target") not in EDIT_TARGETS:
                errors.append(f"edit must be an object with target in {EDIT_TARGETS}: {str(e)[:200]}")
                continue
            target, files = e["target"], e.get("files")
            reason = c.mask(str(e.get("reason") or ""), self.secrets)[:1000]
            if target in versions.trial_state(self.harness_root):
                errors.append(f"{target}: 동시 비교 중에는 지금 판을 바꾸지 않음. trial_end로 먼저 끝냄")
                continue
            closing = None
            if target in ("seller", "buyer"):
                closing = (b.version_results(self.root, target, now, last=1, harness_root=self.harness_root) or [None])[-1]
            try:
                ok, info = versions.apply_edit(self.harness_root, target, files, reason, self.secrets, now)
            except (OSError, ValueError) as ex:
                ok, info = False, {"errors": [f"{type(ex).__name__}"]}
            if not ok:
                errors += [f"{target}: {x}" for x in info["errors"]]
                continue
            if closing is not None:
                self.traj.append(
                    "operator",
                    "version_result",
                    target=target,
                    version=closing["version"],
                    since_ts=closing["since_ts"],
                    until_ts=now,
                    metrics=closing["metrics"],
                )
            self.traj.append(
                "operator", "harness_edit", target=target, version=info["version"], files=sorted(files), reason=reason
            )
            applied.append({"target": target, "version": info["version"]})
        return applied, errors

    def _reflect(self, items):
        out = []
        for r in items if isinstance(items, list) else []:
            if not isinstance(r, dict) or r.get("target") not in EDIT_TARGETS:
                continue
            try:
                version = int(r.get("version"))
            except (TypeError, ValueError):
                continue
            effects = []
            for e in r.get("effects") or []:
                if isinstance(e, dict):
                    effect = e.get("effect") if e.get("effect") in versions.EFFECTS else "판단 불가"
                    effects.append(
                        {
                            "edit": c.mask(str(e.get("edit") or ""), self.secrets)[:500],
                            "effect": effect,
                            "evidence": c.mask(str(e.get("evidence") or ""), self.secrets)[:500],
                        }
                    )
            lesson = r.get("lesson")
            lesson = c.mask(str(lesson), self.secrets)[:1000] if lesson else None
            self.traj.append(
                "operator", "reflection", target=r["target"], version=version, effects=effects, lesson=lesson
            )
            if lesson:
                versions.write_experience(self.harness_root, r["target"], version, effects, lesson)
            out.append({"target": r["target"], "version": version, "lesson": bool(lesson)})
        return out

    def _revert(self, items, now):
        out = []
        for r in items if isinstance(items, list) else []:
            if not isinstance(r, dict) or r.get("target") not in EDIT_TARGETS:
                continue
            reason = c.mask(str(r.get("reason") or ""), self.secrets)[:500]
            if r["target"] in versions.trial_state(self.harness_root):
                out.append({"target": r["target"], "ok": False, "errors": ["동시 비교 중에는 되돌리지 않음. trial_end로 먼저 끝냄"]})
                continue
            ok, info = versions.revert(self.harness_root, r["target"], r.get("version"), reason or None, now)
            if ok:
                self.traj.append(
                    "operator",
                    "harness_revert",
                    target=r["target"],
                    version=info["version"],
                    restored_from=info["restored_from"],
                    reason=reason,
                )
            out.append({"target": r["target"], "ok": ok, **info})
        return out

    def trial_timeouts(self, now):
        """End each trial older than versions.TRIAL_MAX_H without adopting it (kernel rule): the current version stays.
        -> list of _trial_end results."""
        out = []
        for target, t in sorted(versions.trial_state(self.harness_root).items()):
            if now - (t.get("started_ts") or now) >= versions.TRIAL_MAX_H * 3600:
                reason = f"커널 시간 상한 {versions.TRIAL_MAX_H}시간: 판정 없이 끝내고 지금 판 유지"
                out.append(self._trial_end({"target": target, "adopt": False, "reason": reason}, now))
        return out

    def _trial_end(self, item, now):
        if not isinstance(item, dict) or item.get("target") not in versions.TRIAL_AGENTS:
            return {"ok": False, "errors": [f"trial_end.target must be one of {versions.TRIAL_AGENTS}"]}
        target, adopt = item["target"], item.get("adopt") is True
        reason = c.mask(str(item.get("reason") or ""), self.secrets)[:500]
        t = versions.trial_state(self.harness_root).get(target)
        closing = b.trial_results(self.root, now, harness_root=self.harness_root) if t else None
        ok, info = versions.end_trial(self.harness_root, target, adopt, reason or None, now)
        if not ok:
            return {"ok": False, **info}
        tr = info["trial"]
        self.traj.append(
            "operator", "trial_end", target=target, version=tr["version"], base=tr["base"], adopt=adopt,
            reason=reason, metrics=(closing or {}).get("arms"),
        )
        if adopt:
            base = (b.version_results(self.root, target, now, last=1, harness_root=self.harness_root) or [None])[-1]
            if base is not None:
                self.traj.append(
                    "operator", "version_result", target=target, version=base["version"], since_ts=base["since_ts"],
                    until_ts=now, metrics=base["metrics"],
                )
            self.traj.append(
                "operator", "harness_edit", target=target, version=info["version"], files=tr.get("files") or [],
                reason=f"동시 비교 채택(후보 판 {tr['version']}): {reason}"[:1000], adopted_from=tr["version"],
            )
        return {"ok": True, "adopted": adopt, "version": info.get("version"), "trial_version": tr["version"]}

    def _trial_start(self, item, now):
        if not isinstance(item, dict) or item.get("target") not in versions.TRIAL_AGENTS:
            return {"ok": False, "errors": [f"trial.target must be one of {versions.TRIAL_AGENTS}"]}
        if not versions.TRIAL_ENABLED:
            return {"ok": False, "errors": ["동시 비교는 꺼져 있음. 판매자 수정은 edits로 바로 적용하고 약 1시간 뒤 결과로 판단함"]}
        reason = c.mask(str(item.get("reason") or ""), self.secrets)[:1000]
        try:
            ok, info = versions.start_trial(
                self.harness_root, item["target"], item.get("files"), item.get("share"), reason, self.secrets, now
            )
        except (OSError, ValueError) as ex:
            ok, info = False, {"errors": [type(ex).__name__]}
        if ok:
            self.traj.append(
                "operator", "trial_start", target=item["target"], version=info["version"], base=info["base"],
                share=info["share"], files=sorted(item.get("files") or {}), reason=reason,
            )
        return {"ok": ok, **info}

    def _restock(self, plan, now):
        if not isinstance(plan, dict):
            return {"ok": False, "error": "restock_plan must be an object"}
        buy = []
        for x in plan.get("buy") or []:
            if (
                isinstance(x, dict)
                and isinstance(x.get("sku"), str)
                and isinstance(x.get("qty"), int)
                and not isinstance(x.get("qty"), bool)
                and x["qty"] > 0
            ):
                buy.append({"sku": x["sku"], "qty": x["qty"]})
        vu = plan.get("valid_until")
        if not isinstance(vu, (int, float)) or isinstance(vu, bool):
            return {"ok": False, "error": "valid_until must be a unix time"}
        doc = {"ts": round(now, 3), "buy": buy, "valid_until": vu}
        path = os.path.join(self.root, "a2/restock_plan.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
        self.traj.append("operator", "restock_plan", buy=buy, valid_until=vu)
        return {"ok": True, "buy": len(buy)}

    def _forum(self, posts):
        posts = [
            {"post": c.mask(p["post"], self.secrets), "as": p["as"]}
            for p in (posts if isinstance(posts, list) else [])
            if isinstance(p, dict) and isinstance(p.get("post"), str) and p.get("as") in ("shop", "buyer")
        ]
        if not posts:
            return {"queued": 0}
        try:
            forum = importlib.import_module("kernel.forum")
        except ImportError:
            self.traj.append("operator", "forum", status="unavailable", n=len(posts))
            return {"queued": 0, "status": "forum unavailable"}
        res = forum.queue(self.root, posts)  # logs one row per post itself
        return {"queued": sum(1 for r in res or [] if isinstance(r, dict) and r.get("ok")), "result": res}

    def _memcheck(self):
        if self.dry:
            self.traj.append("operator", "memcheck", status="dry")
            return {"started": False}
        self.spawn(
            [sys.executable, "-m", "kernel.memcheck", "--root", self.root],
            cwd=HERE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.traj.append("operator", "memcheck", status="started")
        return {"started": True}

    def _drop_unused(self, done, result):
        """LIVE off: attempt 2's seller and buyer harness, trials and restock plans do not reach the shop."""
        done = dict(done)
        dropped = []
        edits = done.get("edits")
        if isinstance(edits, list):
            keep = [e for e in edits if not (isinstance(e, dict) and e.get("target") in ("seller", "buyer"))]
            if len(keep) != len(edits):
                dropped.append(f"edits seller/buyer {len(edits) - len(keep)}개")
            done["edits"] = keep
        for k in ("trial", "trial_end", "restock_plan", "revert"):
            if k in done and (k != "revert" or any(
                isinstance(r, dict) and r.get("target") in ("seller", "buyer") for r in done.get("revert") or []
            )):
                if k == "revert":
                    done["revert"] = [r for r in done["revert"] if not (isinstance(r, dict) and r.get("target") in ("seller", "buyer"))]
                else:
                    done.pop(k)
                dropped.append(k)
        if dropped:
            result["not_applied_live_off"] = dropped
        return done

    def _policy(self, items, now):
        if self.live():
            return [{"ok": False, "error": "LIVE 켜짐: policy는 시도 1 규칙이 운영할 때만 고침"}]
        cats = {v.get("category") for v in b._catalog(self.root).values() if isinstance(v, dict) and v.get("category")}
        return policy_edit.apply(self.root, items, self.traj, now, categories=cats)

    def finish(self, done, prompt, route, now):
        """Apply a done action. Edit format errors go back to the model once for one corrected try."""
        result = {"note": c.mask(str(done.get("note") or ""), self.secrets)[:500]}
        result["reflection"] = self._reflect(done.get("reflection"))
        if not self.live():
            done = self._drop_unused(done, result)
        if "trial_end" in done:
            result["trial_end"] = self._trial_end(done["trial_end"], now)
        result["revert"] = self._revert(done.get("revert"), now)
        applied, errors = self._apply_edits(done.get("edits"), now)
        result["edit_errors"] = errors
        if errors:
            retry_prompt = (
                prompt
                + "\n\n## 방금 낸 done\n\n"
                + json.dumps({"edits": done.get("edits")}, ensure_ascii=False)[:20_000]
                + "\n\n## 형식 검사 오류 (한 번 더 고칠 수 있음)\n\n"
                + "\n".join(f"- {e}" for e in errors)
                + '\n\n걸린 수정만 고쳐 {"done": {"edits": [...]}}로 답함. 고치지 않으려면 edits를 빈 목록으로 줌.'
            )
            try:
                again = parse_last_object(self._ask(retry_prompt, route)).get("done") or {}
                more, result["retry_errors"] = self._apply_edits(again.get("edits"), now)
                applied += more
            except (ModelRefused, ModelError, ParseError) as e:
                result["retry_errors"] = [f"retry failed: {type(e).__name__}"]
        result["edits"] = applied
        if "trial" in done:
            result["trial"] = self._trial_start(done["trial"], now)
        if isinstance(done.get("settings"), dict):
            cur = c.load_json(os.path.join(self.harness_root, "operator", "settings.json"), {}) or {}
            new = dict(cur, **done["settings"])
            ok, info = versions.apply_edit(
                self.harness_root,
                "operator",
                {"settings.json": json.dumps(new, ensure_ascii=False, indent=1)},
                "운영자 설정 변경",
                self.secrets,
                now,
            )
            if ok:
                self.traj.append(
                    "operator", "harness_edit", target="operator", version=info["version"], files=["settings.json"],
                    reason="운영자 설정 변경",
                )
            result["settings"] = {"ok": ok, **info, "bounded": bounded(new)}
        if "restock_plan" in done:
            result["restock_plan"] = self._restock(done["restock_plan"], now)
        if "forum" in done:
            result["forum"] = self._forum(done["forum"])
        if "policy" in done:
            result["policy"] = self._policy(done["policy"], now)
        if done.get("memcheck") is True:
            result["memcheck"] = self._memcheck()
        return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--once", action="store_true", help="one wake now, then exit")
    ap.add_argument("--dry", action="store_true", help="stub model that answers done with no edits")
    ap.add_argument("--harness", default=HARNESS)
    ap.add_argument("--cli", default=CLI)
    ap.add_argument("--role", default=None, help="shop or buyer (env A2_OPERATOR_ROLE); none = the single operator")
    a = ap.parse_args(argv)
    role = a.role or os.environ.get("A2_OPERATOR_ROLE") or None
    if role is not None and role not in ROLES:
        ap.error(f"--role must be one of {', '.join(ROLES)}")
    root = c.data_root(a.root)
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=secrets, role=role)
    if a.dry:
        terra = DryTerra()
    else:
        from kernel.llm import Terra

        terra = Terra(traj, cli=a.cli, secrets=secrets)
    op = Operator(root, terra, traj, harness_root=a.harness, dry=a.dry, role=role)
    poll_err = (None, 0.0)
    while True:
        try:  # rule-code changes: restart, canary and verdict (code only, never blocks)
            if role is None:
                code_events = [e for e in code_edit.watch(root, c.now_ts(), traj) if e.get("wake")]
            elif role == "shop":  # one process runs restarts; verdicts reach each role through the ledger (wake_reason)
                with op._ledger_lock():
                    code_events = [e for e in code_edit.watch(root, c.now_ts(), traj)
                                   if e.get("wake") and e.get("status") in code_edit.OPEN]
            else:
                code_events = []
        except Exception:
            code_events = []
        try:
            versions.check_breakage(root, a.harness, traj, op.settings()["breakage_n"])
            if not op.compact():  # compact mode ticks the ledger inside wake_reason; verdicts must not stop elsewhere
                with op._ledger_lock():
                    ex.tick(root, c.now_ts(), traj, categories=op._cats())
            reason = "수동 실행 (--once)" if a.once else op.wake_reason()
        except Exception as err:  # one bad poll must not stop the daemon; the same error is logged once an hour
            if a.once:
                raise
            reason, name, t = None, type(err).__name__, time.time()
            if name != poll_err[0] or t - poll_err[1] >= 3600:
                poll_err = (name, t)
                traj.append("operator", "operator_poll", status="crash", error=name, detail=str(err)[:200])
        if code_events:  # like an experiment verdict: wake at once
            reason = ", ".join([f"코드 수정 판정 {len(code_events)}건 (상태 카드의 [코드] 줄)"] + ([reason] if reason else []))
        if reason and role is not None and not claim_busy(root, role, c.now_ts()):
            reason = None  # the other role's wake runs: this one waits and asks again at the next poll
        if reason:
            try:
                print(json.dumps(op.wake(reason), ensure_ascii=False, default=str), flush=True)
            except Exception as err:  # one bad wake must not stop the daemon
                if a.once:
                    raise
                traj.append("operator", "operator_wake", reason=reason, status="crash", error=type(err).__name__)
            finally:
                if role is not None:
                    release_busy(root, role)
        if op.compact() and os.environ.get("A2_AUDITOR") == "1" and not a.dry:
            # meta-harness: audits how the operator works, edits its guidance and tool list, never policy
            try:
                if role is None:
                    auditor.maybe_run(root, terra, traj, c.now_ts(), secrets=secrets, listed=[n for n, _ in op.listed()])
                else:  # one auditor for both roles: whichever process holds the lock runs it when it is due
                    with _flock(root, AUDITOR_LOCK, block=False) as got:
                        if got:
                            auditor.maybe_run(root, terra, traj, c.now_ts(), secrets=secrets,
                                              listed=[n for n, _ in op.listed()], roles=ROLES)
            except Exception:  # the auditor must never stop the operator daemon
                pass
        if a.once:
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
