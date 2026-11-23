"""Operator-made tools (human code, not editable by the model): read-only queries over the shop's logs.

A made tool is a JSON spec: filter (where) -> group (group_by) -> aggregate (agg) over one JSONL source in
a time window, or a saved code tool (kind "code": def run(load, args) run by kernel.code_tools in its sandbox).
Fields are dotted paths resolved by dict/list indexing only (no getattr, no eval), sources are a fixed name -> path
table, and the only file written is the registry <root>/a2/tools_made.json. A meta-harness prunes tools
through remove().
"""

import json
import math
import os
import re
import statistics as st
from datetime import datetime

from kernel import code_tools
from kernel import common as c

STORE = "a2/tools_made.json"
MADE_MAX = 100  # runaway backstop, not shown to the model
PREFIX = "q_"
OUT_CHARS = 4000
MAX_BYTES = 20_000_000  # tail read per run
MAX_ROWS = 200_000  # rows scanned per run
CELL_CHARS = 60  # per string cell in the output (log text is untrusted)
SAMPLE_CHARS = 600
SPEC_CHARS = 4000  # whole spec as JSON

SOURCES = {
    "events": ".r3/events.jsonl",
    "metrics": ".r3/metrics.jsonl",
    "buyer": "logs/buyer_attempts.jsonl",
    "trajectory": "a2/trajectory.jsonl",
}
OPS = ("==", "!=", ">", ">=", "<", "<=", "in", "contains", "exists")
FNS = ("count", "sum", "mean", "median", "min", "max", "share")
DERIVED = ("hour", "leaf")
KEYS = (
    "name",
    "description",
    "source",
    "hours",
    "where",
    "group_by",
    "agg",
    "sort",
    "limit",
)
CODE_KEYS = ("name", "description", "code")  # a saved code tool
META = ("created_ts", "by", "uses", "last_used_ts", "role")  # role: the role operator that made it (shared by both)
# Identity fields hashed in output cells, as common.scrub does for the other tools.
ID_KEYS = c.IDENTITY_KEYS + ("author", "shop", "buyer", "seller", "login", "counterparty", "participants", "from", "to")

_NAME = re.compile(r"^q_[a-z0-9_]{3,30}$")
_SEG = re.compile(r"^[A-Za-z0-9_]+$")
_LABEL = re.compile(r"^[A-Za-z0-9_가-힣]{1,24}$")
_WS = re.compile(r"[\r\n\t\x00-\x1f]+")


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _scalar(x):
    return (
        x is None
        or isinstance(x, (bool, int, float))
        or (isinstance(x, str) and len(x) <= 100)
    )


def _path_err(p, derived=False):
    if derived and p in DERIVED:
        return None
    if not isinstance(p, str) or not p or len(p) > 80:
        return f"필드 경로는 1..80자 문자열: {p!r}"[:120]
    segs = p.split(".")
    if len(segs) > 4:
        return f"필드 경로 깊이는 4 이하: {p}"
    for s in segs:
        if not _SEG.fullmatch(s) or s.startswith("__"):
            return f"필드 경로 조각은 영문/숫자/_ 만, __ 시작 금지: {p}"
    return None


def validate(spec):
    """Strict check of a made-tool spec. Returns (clean_spec, None) or (None, error)."""
    if not isinstance(spec, dict):
        return None, "spec은 객체(dict)여야 함"
    try:
        size = len(json.dumps(spec, ensure_ascii=False))
    except (TypeError, ValueError):
        return None, "spec은 JSON으로 직렬화되어야 함"
    if size > SPEC_CHARS:
        return None, f"spec이 너무 큼: {size}자 > {SPEC_CHARS}"
    extra = sorted(str(k) for k in spec if k not in KEYS)
    if extra:
        return None, f"모르는 키: {', '.join(extra)} (허용: {', '.join(KEYS)})"
    name = spec.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None, f"name은 {PREFIX}로 시작하는 ^q_[a-z0-9_]{{3,30}}$ 형식이어야 함"
    desc = spec.get("description")
    if not isinstance(desc, str) or not 10 <= len(desc.strip()) <= 200:
        return None, "description은 10..200자 (어떤 숫자를 주는지)"
    if spec.get("source") not in SOURCES:
        return None, f"source는 {', '.join(SOURCES)} 중 하나"
    hours = spec.get("hours")
    if not _num(hours) or not 1 <= hours <= 48:
        return None, "hours는 1..48 숫자"

    where = spec.get("where", [])
    if not isinstance(where, list) or len(where) > 6:
        return None, "where는 조건 6개 이하의 목록"
    clean_where = []
    for w in where:
        if not isinstance(w, dict) or set(w) - {"field", "op", "value"}:
            return None, "where 항목은 {field, op, value}만"
        err = _path_err(w.get("field"), derived=True)
        if err:
            return None, err
        op, v = w.get("op"), w.get("value")
        if op not in OPS:
            return None, f"op는 {' '.join(OPS)} 중 하나"
        if op == "in":
            if (
                not isinstance(v, list)
                or not 1 <= len(v) <= 20
                or not all(_scalar(x) for x in v)
            ):
                return None, "in의 value는 스칼라 1..20개 목록"
        elif op == "exists":
            if not isinstance(v, bool):
                return None, "exists의 value는 true/false"
        elif op == "contains":
            if not isinstance(v, str) or not v or len(v) > 100:
                return None, "contains의 value는 1..100자 문자열"
        elif op in ("==", "!="):
            if not _scalar(v):
                return None, "==, != 의 value는 스칼라(문자열은 100자 이하)"
        elif not _num(v):
            return None, f"{op}의 value는 숫자"
        clean_where.append({"field": w["field"], "op": op, "value": v})

    group_by = spec.get("group_by", [])
    if not isinstance(group_by, list) or len(group_by) > 3:
        return None, "group_by는 3개 이하의 목록"
    for g in group_by:
        err = _path_err(g, derived=True)
        if err:
            return None, err

    agg = spec.get("agg")
    if not isinstance(agg, list) or not 1 <= len(agg) <= 5:
        return None, "agg는 1..5개 목록"
    clean_agg, labels = [], []
    for a in agg:
        if not isinstance(a, dict) or set(a) - {"fn", "field", "as"}:
            return None, "agg 항목은 {fn, field, as}만"
        fn, field = a.get("fn"), a.get("field")
        if fn not in FNS:
            return None, f"fn은 {' '.join(FNS)} 중 하나"
        if field is not None or fn != "count":
            err = _path_err(field, derived=True)
            if err:
                return None, err
        label = a.get("as")
        if label is None:
            label = fn if field is None else f"{fn}_{field.split('.')[-1]}"
        if not isinstance(label, str) or not _LABEL.fullmatch(label):
            return None, "as는 1..24자 (영문/숫자/_/한글)"
        if label in labels:
            return None, f"agg 이름 중복: {label} (as로 구분)"
        labels.append(label)
        item = {"fn": fn, "as": label}
        if field is not None:
            item["field"] = field
        clean_agg.append(item)

    sort = spec.get("sort")
    clean_sort = None
    if sort is not None:
        if not isinstance(sort, dict) or set(sort) - {"by", "desc"}:
            return None, "sort는 {by, desc}만"
        by, down = sort.get("by", 0), sort.get("desc", True)
        if isinstance(by, bool) or not (
            by == "key"
            or by in labels
            or (isinstance(by, int) and 0 <= by < len(clean_agg))
        ):
            return None, "sort.by는 agg 이름, agg 순번(0부터) 또는 'key'"
        if not isinstance(down, bool):
            return None, "sort.desc는 true/false"
        clean_sort = {"by": labels[by] if isinstance(by, int) else by, "desc": down}

    limit = spec.get("limit", 15)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
        return None, "limit은 1..30 정수"

    return {
        "name": name,
        "description": " ".join(desc.split()),
        "source": spec["source"],
        "hours": hours,
        "where": clean_where,
        "group_by": list(group_by),
        "agg": clean_agg,
        "sort": clean_sort,
        "limit": limit,
    }, None


def validate_code(spec):
    """Check of a code tool {name, description, code}. Returns (clean_spec, None) or (None, error)."""
    if not isinstance(spec, dict):
        return None, "spec은 객체(dict)여야 함"
    extra = sorted(str(k) for k in spec if k not in CODE_KEYS)
    if extra:
        return None, f"모르는 키: {', '.join(extra)} (코드 도구는 {', '.join(CODE_KEYS)})"
    name = spec.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None, f"name은 {PREFIX}로 시작하는 ^q_[a-z0-9_]{{3,30}}$ 형식이어야 함"
    desc = spec.get("description")
    if not isinstance(desc, str) or not 10 <= len(desc.strip()) <= 200:
        return None, "description은 10..200자 (어떤 숫자를 주는지)"
    err = code_tools.check(spec.get("code"))
    if err:
        return None, err
    return {"name": name, "description": " ".join(desc.split()), "code": spec["code"], "kind": "code"}, None


def _get(row, path):
    """Value at a dotted path by dict keys and digit list indexes only; None when missing. Derived: hour, leaf."""
    if path == "hour":
        ts = row.get("ts")
        return datetime.fromtimestamp(ts, c.KST).strftime("%H") if _num(ts) else None
    if path == "leaf":
        cat = row.get("category")
        if not isinstance(cat, str):
            d = row.get("data")
            cat = d.get("category") if isinstance(d, dict) else None
        return (cat.split(">")[-1].strip() or None) if isinstance(cat, str) else None
    cur = row
    for seg in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return None
    return cur


def _match(row, w):
    v, op, want = _get(row, w["field"]), w["op"], w["value"]
    if op == "exists":
        return (v is not None) == want
    if op == "==":
        return v == want
    if op == "!=":
        return v != want
    if op == "in":
        return _hashable(v) and v in want
    if op == "contains":
        if isinstance(v, str):
            return want in v
        return isinstance(v, list) and want in v
    if not _num(v):
        return False
    return {">": v > want, ">=": v >= want, "<": v < want, "<=": v <= want}[op]


def _hashable(v):
    return v is None or isinstance(v, (str, int, float, bool))


def _agg(fn, rows, field):
    if fn == "count":
        return (
            len(rows)
            if field is None
            else sum(1 for r in rows if _get(r, field) is not None)
        )
    vals = [_get(r, field) for r in rows]
    if fn == "share":
        return sum(1 for v in vals if v) / len(vals) if vals else None
    nums = [v for v in vals if _num(v)]
    if not nums:
        return None
    if fn == "sum":
        return sum(nums)
    if fn == "mean":
        return st.fmean(nums)
    if fn == "median":
        return st.median(nums)
    return min(nums) if fn == "min" else max(nums)


def _value(x):
    if x is None:
        return "-"
    if isinstance(x, int) or (
        isinstance(x, float) and x.is_integer() and abs(x) >= 1000
    ):
        return f"{int(x):,}"
    if abs(x) >= 1000:
        return f"{round(x):,}"
    return f"{x:.3g}"


def _cell(path, v, login):
    """One group-key cell: identities hashed, secrets masked, whitespace flattened, CELL_CHARS max."""
    if v is None:
        return "-"
    if isinstance(v, (dict, list)):  # a whole object as a group key could carry names: never printed
        return "(객체: 안쪽 필드를 경로로 지정할 것)"
    segs = path.lower().split(".")
    if any(k in seg for seg in segs for k in ID_KEYS) and v != "":
        s = (
            str(v)
            if isinstance(v, str)
            else json.dumps(v, ensure_ascii=False, default=str)
        )
        v = "우리" if login and login in s.lower() else c.hid(s)
    elif isinstance(v, str):
        v = c.mask(v, (login,))
    elif not isinstance(v, (int, float, bool)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    s = _WS.sub(" ", str(v))
    return s if len(s) <= CELL_CHARS else s[: CELL_CHARS - 1] + "…"


def _key(v):
    return (
        v
        if _hashable(v)
        else json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    )


def _run(root, spec, args, now):
    """(text, problem) where problem is None or why the run went over its bounds."""
    hours = spec["hours"]
    if args:
        hours = args["hours"]
    now = c.now_ts() if now is None else now
    lo = now - hours * 3600
    path = os.path.join(root, SOURCES[spec["source"]])
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    groups, order = {}, []
    scanned = matched = 0
    first_ts = None
    problem = None
    for row in c.tail_json(path, MAX_BYTES):
        if scanned >= MAX_ROWS:
            problem = f"검사 행 한도 {MAX_ROWS:,} 초과"
            break
        scanned += 1
        ts = row.get("ts")
        if not _num(ts):
            continue
        if first_ts is None:
            first_ts = ts
        if not lo <= ts <= now or not all(_match(row, w) for w in spec["where"]):
            continue
        matched += 1
        k = tuple(_key(_get(row, g)) for g in spec["group_by"])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(row)
    if problem is None and size > MAX_BYTES and first_ts is not None and first_ts > lo:
        problem = (
            f"읽기 한도 {MAX_BYTES // 1_000_000}MB가 {hours}시간 창 전체를 덮지 못함"
        )

    labels = [a["as"] for a in spec["agg"]]
    table = [
        (k, [_agg(a["fn"], groups[k], a.get("field")) for a in spec["agg"]])
        for k in order
    ]
    sort = spec["sort"] or {"by": labels[0], "desc": True}
    if sort["by"] == "key":
        table.sort(key=lambda t: [str(x) for x in t[0]], reverse=sort["desc"])
    else:
        i = labels.index(sort["by"])
        have = [t for t in table if t[1][i] is not None]
        have.sort(key=lambda t: t[1][i], reverse=sort["desc"])
        table = have + [t for t in table if t[1][i] is None]

    login = c.owner_login(root)
    head = (
        f"{spec['name']}: {spec['source']} 최근 {_value(hours)}시간 ({c.kst(lo)} ~ {c.kst(now)} KST), "
        f"일치 {matched:,}행 / 검사 {scanned:,}행, 그룹 {len(table)}"
    )
    lines = [head]
    if problem:
        lines.append(f"[주의: {problem}. 결과가 불완전함, hours를 줄일 것]")
    if not table:
        lines.append("(조건에 맞는 행 없음)")
    shown, used = 0, len("\n".join(lines))
    tail_room = 60
    for k, vals in table[: spec["limit"]]:
        key = (
            " / ".join(_cell(g, v, login) for g, v in zip(spec["group_by"], k))
            or "전체"
        )
        line = f"{key}: " + ", ".join(
            f"{lab}={_value(v)}" for lab, v in zip(labels, vals)
        )
        if used + 1 + len(line) > OUT_CHARS - tail_room:
            break
        lines.append(line)
        used += 1 + len(line)
        shown += 1
    if shown < len(table):
        lines.append(
            f"[그룹 {len(table) - shown}개 잘림: limit {spec['limit']}, 출력 {OUT_CHARS}자 한도]"
        )
    return "\n".join(lines), problem


def _args_err(args):
    if args is None or args == {}:
        return None
    if not isinstance(args, dict):
        return 'args는 {"hours": 1..48}만 허용'
    extra = sorted(str(k) for k in args if k != "hours")
    if extra:
        return f"모르는 인자: {', '.join(extra)} (허용: hours)"
    h = args.get("hours")
    if not _num(h) or not 1 <= h <= 48:
        return "hours는 1..48 숫자"
    return None


def run(root, spec, args=None, now=None):
    """Text table of a made tool over its window. Bad specs or args give an error string; bad rows are skipped."""
    clean, err = validate(spec)
    if err:
        return f"도구 정의 오류: {err}"
    err = _args_err(args)
    if err:
        return f"인자 오류: {err}"
    return _run(root, clean, args or None, now)[0]


def _read_store(root):
    raw = c.load_json(os.path.join(root, STORE), [])
    return raw if isinstance(raw, list) else []


def load(root):
    """Stored tools (spec plus created_ts, by, uses, last_used_ts). A corrupt registry or entry is dropped."""
    out = []
    for t in _read_store(root):
        if not isinstance(t, dict):
            continue
        spec = {k: v for k, v in t.items() if k not in META}
        if t.get("kind") == "code":
            clean, err = validate_code({k: v for k, v in spec.items() if k != "kind"})
        else:
            clean, err = validate(spec)
        if err or any(x["name"] == clean["name"] for x in out):
            continue
        clean.update({k: t[k] for k in META if k in t})
        out.append(clean)
    return out[:MADE_MAX]


def _write(root, tools):
    path = os.path.join(root, STORE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tools, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def listing(root):
    """[(name, description)] of the stored tools."""
    return [(t["name"], t["description"]) for t in load(root)]


def add(root, spec, now, traj=None, by="operator", reserved=(), role=None):
    """Validate, test-run on the real data and store a made tool (tagged with the maker's role when given). Returns
    {"ok", "name", "sample"} or {"ok", "error"}. A spec with code is a code tool: check and one real run are the gate."""
    code = isinstance(spec, dict) and "code" in spec
    clean, err = validate_code(spec) if code else validate(spec)
    if err:
        return {"ok": False, "error": err}
    name = clean["name"]
    if name in set(reserved):
        return {"ok": False, "error": f"{name}은 이미 있는 도구 이름"}
    tools = load(root)
    if any(t["name"] == name for t in tools):
        return {"ok": False, "error": f"{name}은 이미 만든 도구 (먼저 remove)"}
    if len(tools) >= MADE_MAX:
        return {
            "ok": False,
            "error": f"만든 도구는 최대 {MADE_MAX}개: 하나를 지운 뒤 추가",
        }
    if code:
        text, problem = code_tools.execute(root, clean["code"], None, now), None
        if text.startswith(code_tools.ERR):
            return {"ok": False, "error": f"시험 실행 실패: {text[:300]}"}
    else:
        try:
            text, problem = _run(root, clean, None, now)
        except Exception as e:  # the test run is the gate: any failure refuses the tool
            return {
                "ok": False,
                "error": f"시험 실행 실패: {type(e).__name__}: {str(e)[:200]}",
            }
    if problem:
        return {"ok": False, "error": f"시험 실행이 한도를 넘음: {problem}"}
    clean.update({"created_ts": now, "by": by, "uses": 0, **({"role": role} if role else {})})
    _write(root, tools + [clean])
    if traj is not None:
        traj.append(
            by,
            "tool_made",
            name=name,
            source=clean.get("source", "code"),
            description=clean["description"],
        )
    return {"ok": True, "name": name, "sample": text[:SAMPLE_CHARS]}


def remove(root, name, now, traj=None, by="auditor"):
    """Delete a made tool by name and log a tool_removed row."""
    tools = load(root)
    keep = [t for t in tools if t["name"] != name]
    if len(keep) == len(tools):
        return {"ok": False, "error": f"만든 도구 없음: {name}"}
    _write(root, keep)
    if traj is not None:
        traj.append(by, "tool_removed", name=name, ts_removed=now)
    return {"ok": True}


def call(root, name, args=None, now=None, count=True):
    """Run a stored tool by name and count the use (count=False: a kernel read such as the state card, not a use).
    Unknown name or bad args give a Korean error string."""
    tools = load(root)
    tool = next((t for t in tools if t["name"] == name), None)
    if tool is None:
        have = ", ".join(t["name"] for t in tools) or "없음"
        return f"만든 도구 없음: {name}. 있는 도구: {have}"
    if tool.get("kind") == "code":
        text = code_tools.execute(root, tool["code"], args or None, now)
    else:
        err = _args_err(args)
        if err:
            return f"인자 오류: {err}"
        spec = {k: v for k, v in tool.items() if k not in META}
        text = _run(root, spec, args or None, now)[0]
    if not count:
        return text
    try:
        for t in tools:
            if t["name"] == name:
                t["uses"] = (t.get("uses") if _num(t.get("uses")) else 0) + 1
                t["last_used_ts"] = c.now_ts() if now is None else now
        _write(root, tools)
    except Exception:  # use counting is best effort
        pass
    return text
