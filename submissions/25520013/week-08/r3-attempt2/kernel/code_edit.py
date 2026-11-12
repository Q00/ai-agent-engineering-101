"""Operator edits of the R3 rule code (human code, not editable by the model).

The operator may read the rule code (policy.py, hooks/*.py under the data root) and propose small text edits
(done.code). The kernel, not a model, gates them: editable files only, exact unique `old` text, AST checks
(syntax, protected safety functions byte-identical, required guard references kept, no added dangerous imports,
calls, file writes or URLs), the rule code's own tests on a staged copy (no test may newly fail against the
unmodified copy's baseline), then an atomic write with backups.

The seller is a long-running process that imports policy.py, so a change to hooks/seller.py or policy.py needs a
seller restart (A2_SELLER_RESTART_CMD, started without waiting by watch()). Status machine of a change:

    restart_pending --restart ok--> watching          (no restart needed: applied straight to watching)
    restart_pending --restart failed / timeout--> refused_restart (backups restored; old process runs old code)
    watching --canary trips--> reverted | revert_restart_pending | conflict
    watching --WATCH_S passed--> evaluating
    evaluating --EVAL_H passed--> kept, with the before/after numbers (11-11: no revert on the comparison; the
                                  operator reads the numbers and decides. Rows without eval_revert False, written
                                  before that, still revert when worse beyond noise)
    revert_restart_pending --restart ok--> reverted   (failed restarts retried every RETRY_S, files stay restored)
    any open status --MAX_FAILS evaluation errors--> voided (code left as it is)

watch() runs on every operator poll and never blocks: a 30 min canary on the hooks' metrics rows (revert at once on
errors or silence), then a 4 h before/after comparison with the experiment ledger's sequential evaluate (shop score
proxy for shop-side code, buyer saved gain for the buyer hook, both for policy.py), reported, not acted on. A new
change is refused only while another one is restarting or under the canary (BLOCKING).

State: <root>/a2/code_changes.json (list), backups <root>/a2/code_backups/<id>/, stage <root>/a2/code_stage/,
test baseline cache <root>/a2/code_baseline.json, restart logs <root>/a2/logs/seller_restart-<id>.log.
watch, lines, summary and the tools never raise.
"""

import ast
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections import Counter

from kernel import brief
from kernel import common as c
from kernel import experiments as ex

# ------------------------------------------------------------ constants
EDITABLE = ("policy.py", "hooks/restock.py", "hooks/market_buyer.py", "hooks/seller.py")
RESTART_FILES = (
    "hooks/seller.py",
    "policy.py",
)  # the long-running seller imports both: a restart is needed
SHOP_FILES = ("hooks/restock.py", "hooks/seller.py")  # judged on the shop metric
BUYER_FILES = (
    "hooks/market_buyer.py",
)  # judged on the buyer metric; policy.py on both
ROLE_FILES = {  # what each role operator (kernel.operator --role) may change
    "shop": ("policy.py", "hooks/restock.py", "hooks/seller.py"),
    "buyer": ("policy.py", "hooks/market_buyer.py"),
}
ROLE_KO = {"shop": "가게 운영자(shop)", "buyer": "구매 운영자(buyer)"}
# Names whose top-level definition ("Class.method" for a method) must stay byte-identical and may not gain new
# bindings anywhere in the file.
PROTECTED = {
    "policy.py": (
        "policy_path",
        "load",
        "validate",
        "_check",  # which policy file is read and its bounds validation
        "as_int_price",
        "guard_price",
        "floor_price",
        "endgame_floor",  # never sell at or below cost
        "liquidating",  # liquidation line
        "receipt_terms",
        "food_grade_now",  # honest grade claim on the receipt
        "buyer_terms",
        "choose_offers",  # buyer max_pay = min(budget, list x max_pay_frac from the policy)
        "own_shop",
        "own_buyer",  # owner identity: never trade with ourselves
    ),
    "hooks/restock.py": ("main",),  # crash safety net: an empty plan instead of a crash
    "hooks/market_buyer.py": (
        "over_cap",  # last payment check: price <= budget and <= the candidate cap
        "grade_c_terms",  # grade C cap stays under the budget and the policy accept_c cap
        "verify_receipt",
        "RECEIPT_SIGNED",  # seller signature check
    ),
    "hooks/seller.py": (
        "Seller.decide",  # an accept goes through P.guard_price (strictly above cost, int)
        "canon",
        "sign",  # receipt signing with the shop key (credential)
        "resolve_shop_name",
        "ENV_NAME",
        "NAME_FILE_MAX",
        "NAME",  # shop identity
        "Seller._bl",  # buyer log writes go through buyer_log (masked)
    ),
}
# References (names or attributes) whose count in the file may not drop: the call path of the guards above.
GUARD_REFS = {
    "hooks/seller.py": (
        "guard_price",
        "decide",
        "receipt_terms",
        "sign",
        "clean",
        "offer_fields",
    ),
    "hooks/market_buyer.py": ("over_cap", "verify_receipt", "own_shop"),
}
BANNED_MODULES = (
    "subprocess",
    "socket",
    "urllib",
    "requests",
    "http",
    "httpx",
    "ctypes",
    "shutil",
    "importlib",
    "pickle",
    "multiprocessing",
    "threading",
    "asyncio",
)
# A new import must come from this list (pure computation only); everything else is refused, not only BANNED_MODULES.
ALLOWED_IMPORTS = ("math", "re", "json", "time", "statistics", "collections", "itertools", "functools", "datetime", "bisect", "heapq")
BANNED_NAMES = ("eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars", "__builtins__", "breakpoint", "input")
BANNED_OS = ("system", "popen", "remove", "unlink", "rmdir", "kill", "environ")
WRITE_METHODS = ("write_text", "write_bytes")
# Size limits of one change (runaway guards, not strategy): edits per change, chars of one new text, changed lines.
MAX_EDITS = 8
NEW_CHARS = 20_000
MAX_CHANGED_LINES = 400
WATCH_S = 1800
EVAL_H = 4
SILENCE_S = 1200
SELLER_SILENCE_S = 600
ERR_WINDOW_S = 3600
ERR_MIN_ROWS = 10
ERR_SHARE_MIN = 0.10
RETRY_S = 600
MAX_FAILS = 6
RESTART_TIMEOUT_S = 1500
RESTART_ENV = "A2_SELLER_RESTART_CMD"
RESTART_RESULT = os.path.join("a2", "seller_restart_result.json")
TEST_TIMEOUT_S = 300
TEST_CMD = (
    "uv run --project {root} --no-sync --with pytest python -m pytest -q -p no:cacheprovider --no-header -rfE "
    "--continue-on-collection-errors --rootdir=. --confcutdir=."
)  # no -x: the full failed set is compared with the baseline; {root} is the live root, cwd is the stage copy
TEST_CMD_ENV = "A2_CODE_TEST_CMD"
TESTS_DIR_ENV = "A2_CODE_TESTS_DIR"  # when set, the staged tests/ is a copy of this directory
DIR_MAX = 5_000_000  # a top-level directory larger than this is data, not code the tests import
STAGE_KEEP = 3
STAGE_MAX_BYTES = 100_000_000
TOP_FILE_MAX = 1_000_000
FILE_MAX = 5_000_000
TOP_EXT = (".py", ".json", ".toml", ".cfg", ".ini")
SKIP_DIRS = (
    ".r3",
    ".market",
    "a2",
    "logs",
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    ".pytest_cache",
    "node_modules",
    "data",
)
DIFF_CHARS = 6_000
READ_MAX = 200
SEARCH_ROWS = 40
ROW_CHARS = 160
METRICS_BYTES = 20_000_000
OPEN = ("restart_pending", "watching", "evaluating", "revert_restart_pending")
BLOCKING = ("restart_pending", "watching", "revert_restart_pending")  # a new change waits for these (one restart or
# canary at a time, so a canary trip or a restart names one change)


def _blocks(r):
    """Whether open change r makes a new one wait: restarting or under the canary, or a legacy row still judged with
    a revert."""
    st = r.get("status")
    return st in BLOCKING or (st == "evaluating" and r.get("eval_revert") is not False)
STATUS_KO = {
    "restart_pending": "판매자 재시작 대기",
    "watching": "감시",
    "evaluating": "평가",
    "revert_restart_pending": "되돌린 뒤 판매자 재시작 대기",
    "kept": "유지",
    "reverted": "되돌림",
    "refused_restart": "재시작 실패로 되돌림",
    "conflict": "충돌(되돌리지 못함)",
    "voided": "판정 실패, 그대로 둠",
}
CLS_KO = {
    "shape": "형식",
    "short": "근거 짧음",
    "not_editable": "고칠 수 없는 파일",
    "not_read": "읽지 않은 파일",
    "old": "old 불일치",
    "size": "크기 초과",
    "noop": "바뀐 것 없음",
    "open": "진행 중인 수정",
    "cooldown": "대기 시간",
    "repeat": "되돌린 수정 반복",
    "restart": "재시작 명령 없음",
    "syntax": "문법 오류",
    "protected": "보호 함수 변경",
    "banned": "금지 추가",
    "tests": "시험 새 실패",
    "runner": "시험 실행 불가",
    "write": "쓰기 실패",
    "error": "커널 오류",
    "role": "다른 운영자 몫",
}

CHANGES = os.path.join("a2", "code_changes.json")
BASELINE = os.path.join("a2", "code_baseline.json")
BACKUPS = os.path.join("a2", "code_backups")
STAGE = os.path.join("a2", "code_stage")
TRAJ = os.path.join("a2", "trajectory.jsonl")
_PROCS = {}  # (root, change id) -> restart process handle of this daemon


# ------------------------------------------------------------ files
def _num(v):
    return ex._num_ok(v)


def _sha(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _secrets(root):
    login = c.owner_login(root)
    return (login,) if login else ()


def load(root):
    """The change list; [] for a missing or corrupt file."""
    rows = c.load_json(os.path.join(root, CHANGES), [])
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _save(root, rows):
    ex._atomic_json(os.path.join(root, CHANGES), rows, ".code_changes.tmp-")


def role_of(ch):
    """The role operators a change belongs to: the proposing role when stored, else by its files (hooks/market_buyer.py
    buyer, policy.py both, the rest shop)."""
    if ch.get("role") in ROLE_FILES:
        return {ch["role"]}
    out = set()
    for f in ch.get("files") or []:
        out |= {"shop", "buyer"} if f == "policy.py" else {"buyer"} if f in BUYER_FILES else {"shop"}
    return out or {"shop"}


def role_error(files, role):
    """None when the role may change all these files, else the Korean refusal naming the other role."""
    other = "buyer" if role == "shop" else "shop"
    bad = [f for f in files if f not in ROLE_FILES[role]]
    if not bad:
        return None
    return (
        f"{', '.join(str(f)[:80] for f in bad)}는 {ROLE_KO[role]}가 고치지 않는 파일임"
        f"{'(' + ROLE_KO[other] + ' 몫)' if all(f in ROLE_FILES[other] for f in bad) else ''}. "
        f"고칠 수 있는 파일: {', '.join(ROLE_FILES[role])}"
    )


def _atomic_text(path, data, mode=None):
    """Write bytes or text atomically next to path; keeps mode when given."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".code_edit.tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def readable(root):
    """Relative paths the operator may read: policy.py and every hooks/*.py that is a plain file inside root."""
    hooks = os.path.join(root, "hooks")
    names = (
        sorted(n for n in os.listdir(hooks) if n.endswith(".py"))
        if os.path.isdir(hooks)
        else []
    )
    return [
        rel
        for rel in ["policy.py"] + ["hooks/" + n for n in names]
        if _safe(root, rel, check_list=False)
    ]


def _safe(root, rel, check_list=True):
    """Absolute path of rel when it is a readable rule-code file (no absolute path, no .., no symlink anywhere on the
    way, inside root), else None."""
    if (
        not isinstance(rel, str)
        or not rel
        or "\\" in rel
        or "\x00" in rel
        or os.path.isabs(rel)
    ):
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if not (
        rel == "policy.py"
        or (len(parts) == 2 and parts[0] == "hooks" and parts[1].endswith(".py"))
    ):
        return None
    if check_list and rel not in readable(root):
        return None
    real_root = os.path.realpath(root)
    cur = real_root
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return None
    if not os.path.isfile(cur) or os.path.realpath(cur) != cur:
        return None
    return cur


def _read_text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def needs_restart(files):
    return any(f in RESTART_FILES for f in files)


# ------------------------------------------------------------ tools
def search(root, pattern, file=None):
    """Rows `file:line: text` matching pattern (regex, or substring when the regex is bad) over the readable files."""
    try:
        if not isinstance(pattern, str) or not pattern:
            return "인자 오류: pattern은 비어 있지 않은 글이어야 함"
        if file is not None:
            if _safe(root, file) is None:
                return f"인자 오류: 읽을 수 없는 파일 {str(file)[:80]}. 읽을 수 있는 파일: {', '.join(readable(root))}"
            files = [file]
        else:
            files = readable(root)
        try:
            hit, how = re.compile(pattern).search, "정규식"
        except re.error:
            hit, how = (lambda s: pattern in s), "글자"
        rows, total = [], 0
        for rel in files:
            for i, line in enumerate(_read_text(_safe(root, rel)).splitlines(), 1):
                if hit(line):
                    total += 1
                    if len(rows) < SEARCH_ROWS:
                        rows.append(f"{rel}:{i}: {line.strip()[:ROW_CHARS]}")
        head = f"코드 찾기 ({how}) {pattern[:80]!r}: {total}줄" + (
            f", 앞 {SEARCH_ROWS}줄만 보임" if total > SEARCH_ROWS else ""
        )
        return c.cap(c.mask("\n".join([head] + rows), _secrets(root)), 16_000)
    except Exception as e:  # noqa: BLE001 - a tool answers, it never raises
        return f"도구 오류: {type(e).__name__}: {str(e)[:200]}"


READ_CHARS = 9_000  # one answer must fit what the operator is shown (operator.OBS_CAP["code_read"]), so nothing is cut


def _func_span(text, func):
    """(first line, last line) of the top-level or nested def/class named func, decorators included; None if absent."""
    import ast

    for node in ast.walk(ast.parse("\n".join(text))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == func:
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            return first, node.end_lineno
    return None


def read(root, file, start=1, lines=120, func=None):
    """Numbered lines of one readable file: from start for at most lines (READ_MAX), or the whole function func.
    The answer stops at READ_CHARS on a line boundary and always says where the next call continues."""
    try:
        p = _safe(root, file)
        if p is None:
            return f"인자 오류: 읽을 수 없는 파일 {str(file)[:80]}. 읽을 수 있는 파일: {', '.join(readable(root))}"
        text = _read_text(p).splitlines()
        what, stop = "", len(text)
        if isinstance(func, str) and func.strip():
            name = func.strip().split("(")[0].split(".")[-1]
            span = _func_span(text, name)
            if span is None:
                return f"인자 오류: {file}에 함수 {name[:60]} 없음. code_search로 'def '를 찾아 이름을 확인함"
            if not _num(start) or int(start) <= span[0] or int(start) > span[1]:
                start = span[0]  # a start inside the function continues it
            stop, lines = span[1], READ_MAX
            what = f"함수 {name} {span[0]}~{span[1]}줄 중 "
        start = int(start) if _num(start) else 1
        lines = int(lines) if _num(lines) else 120
        start, lines = max(1, start), max(1, min(READ_MAX, lines))
        end = min(stop, start + lines - 1)
        body, used = [], 0
        for i in range(start, end + 1):
            row = f"{i:>5}| {text[i - 1]}"
            if body and used + len(row) + 1 > READ_CHARS:
                end = i - 1
                break
            body.append(row)
            used += len(row) + 1
        out = [
            f"{file} 전체 {len(text)}줄, {what}{start}~{end}줄"
            + (" (편집 가능)" if file in EDITABLE else " (읽기 전용)")
        ] + body
        if end < stop:
            more = f"start={end + 1}" + (f", func={func.strip()}" if what else "")
            out.append(f"[여기까지 온전히 보임. 이어 읽기: {more}]")
        elif what:
            out.append("[함수 끝]")
        return c.cap(c.mask("\n".join(out), _secrets(root)), 16_000)
    except Exception as e:  # noqa: BLE001
        return f"도구 오류: {type(e).__name__}: {str(e)[:200]}"


def restock_log(root, hours=2, now=None):
    """Restock hook runs by why, the buying runs, units bought per leaf and SKU, cap and budget stops."""
    try:
        now = c.now_ts() if now is None else now
        hours = max(0.5, min(24.0, float(hours))) if _num(hours) else 2.0
        since = now - hours * 3600
        runs = []
        for m in c.tail_json(
            os.path.join(root, ".r3/metrics.jsonl"), METRICS_BYTES, b'"restock_plan"'
        ):
            ts = m.get("ts")
            if (
                m.get("hook") != "restock"
                or m.get("decision") != "restock_plan"
                or not _num(ts)
                or not since <= ts <= now
            ):
                continue
            runs.append(
                (ts, m.get("outcome") if isinstance(m.get("outcome"), dict) else {})
            )
        runs.sort(key=lambda r: r[0])
        whys = Counter(str(o.get("why")) if o.get("why") else "구매함" for _, o in runs)
        out = [
            f"재입고 실행 최근 {hours:g}시간: {len(runs)}회, 사유별 "
            + (", ".join(f"{k} {n}" for k, n in whys.most_common()) or "없음")
        ]
        buys = [(ts, o) for ts, o in runs if not o.get("why")]
        out.append(
            f"산 실행 {len(buys)}회" + (" (최근 것부터 12회)" if len(buys) > 12 else "")
        )
        for ts, o in buys[-12:][::-1]:
            notes = o.get("notes") if isinstance(o.get("notes"), list) else []
            out.append(
                f"- {c.kst(ts)} 줄 {o.get('lines')}, 수량 {o.get('qty')}, 예산 {c.fmt(o.get('budget'))}"
                + (
                    f", 메모: {' / '.join(str(x)[:80] for x in notes[:3])}"
                    if notes
                    else ""
                )
            )
        cat, sku, names = Counter(), Counter(), {}
        nb = int(brief.NET_BYTES_PER_H * (hours + 2))
        for e in c.tail_json(os.path.join(root, ".r3/events.jsonl"), nb, b'"restock"'):
            ts = e.get("ts")
            if (
                e.get("type") != "restock"
                or not e.get("mine")
                or not _num(ts)
                or not since <= ts <= now
            ):
                continue
            d = e.get("data") if isinstance(e.get("data"), dict) else {}
            cat[brief._leaf(d.get("category"))] += 1
            sku[d.get("sku")] += 1
            names[d.get("sku")] = str(d.get("name") or "")[:30]
        out.append(
            f"산 단위 {sum(cat.values())}개, 상품군 잎별: "
            + (", ".join(f"{k} {n}" for k, n in cat.most_common(15)) or "없음")
        )
        out += [f"  {k} {names.get(k, '')}: {n}개" for k, n in sku.most_common(15)]
        last = runs[-1][1] if runs else {}
        out.append(
            f"재고 상한(inventory_cap)으로 멈춤 {whys.get('inventory_cap', 0)}회, 예산(budget)으로 멈춤 "
            f"{whys.get('budget', 0)}회, 마지막 inventory_cost {c.fmt(last.get('inventory_cost'))}, "
            f"잔고 {c.fmt(last.get('balance'))}"
        )
        warns = [
            w
            for _, o in runs
            for w in (o.get("warnings") if isinstance(o.get("warnings"), list) else [])
        ]
        if warns:
            out.append(f"경고 {len(warns)}건, 마지막: {str(warns[-1])[:160]}")
        return c.cap(c.mask("\n".join(out), _secrets(root)), 16_000)
    except Exception as e:  # noqa: BLE001
        return f"도구 오류: {type(e).__name__}: {str(e)[:200]}"


# ------------------------------------------------------------ static checks
def _segments(body, text, name):
    """Source segments of the statements in body that define or assign name ("Class.method" looks into classes)."""
    if "." in name:
        cls, meth = name.split(".", 1)
        out = []
        for node in body:
            if isinstance(node, ast.ClassDef) and node.name == cls:
                out += _segments(node.body, text, meth)
        return out
    out = []
    for node in body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        ):
            out.append(ast.get_source_segment(text, node, padded=True))
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(n, ast.Name) and n.id == name
                for t in targets
                for n in ast.walk(t)
            ):
                out.append(ast.get_source_segment(text, node, padded=True))
    return out


def _bind_counts(tree, names):
    """How often each name is (re)bound anywhere: def/class names, assignment targets, imports as, global, del."""
    n = Counter()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            n[node.name] += 1
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node.id in names
        ):
            n[node.id] += 1
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node.attr in names
        ):
            n[node.attr] += 1
        elif (
            isinstance(node, ast.alias)
            and (node.asname or node.name.split(".")[0]) in names
        ):
            n[node.asname or node.name.split(".")[0]] += 1
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for x in node.names:
                if x in names:
                    n[x] += 1
    return n


def _refs(tree, names):
    n = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names:
            n[node.id] += 1
        elif isinstance(node, ast.Attribute) and node.attr in names:
            n[node.attr] += 1
    return n


def protected_error(rel, before, after):
    """None when every protected name of rel keeps its exact source and gains no new binding, and no guard
    reference of rel was dropped."""
    names = PROTECTED.get(rel, ())
    refs = GUARD_REFS.get(rel, ())
    if not names and not refs:
        return None
    tb, ta = ast.parse(before), ast.parse(after)
    changed = [
        n
        for n in names
        if _segments(tb.body, before, n) != _segments(ta.body, after, n)
    ]
    short = {n.rsplit(".", 1)[-1] for n in names}
    cb, ca = _bind_counts(tb, short), _bind_counts(ta, short)
    changed += [
        n
        for n in names
        if ca[n.rsplit(".", 1)[-1]] > cb[n.rsplit(".", 1)[-1]] and n not in changed
    ]
    if changed:
        return f"{rel}의 보호 함수 {', '.join(changed)}는 고칠 수 없음 (원가 이하 판매 금지, 정책 검증, 지출 상한, 서명, 주인 확인)"
    rb, ra = _refs(tb, refs), _refs(ta, refs)
    dropped = [n for n in refs if ra[n] < rb[n]]
    if dropped:
        return f"{rel}에서 안전 장치 호출을 뺄 수 없음: {', '.join(dropped)}"
    return None


def _is_write_mode(node):
    mode = (
        node.args[1]
        if len(node.args) >= 2
        else next((k.value for k in node.keywords if k.arg == "mode"), None)
    )
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(ch in mode.value for ch in "wax+")
    return True  # a mode the check cannot read counts as a write


def danger(text):
    """Counter of risky constructs in a source text (see BANNED_*): imports, names, os attributes, file writes,
    URL strings."""
    out = Counter()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    out["import " + a.name.split(".")[0]] += 1
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod not in ALLOWED_IMPORTS:
                out["import " + (mod or ".")] += 1
            for a in node.names:
                if mod == "os" and a.name in BANNED_OS:
                    out["os." + a.name] += 1
                if mod == "builtins" and a.name in BANNED_NAMES:
                    out[a.name] += 1
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            out[node.id] += 1
        elif isinstance(node, ast.Name) and node.id in ("os", "sys"):
            out[node.id + " 사용"] += 1  # any new use of os or sys (os.path excepted below)
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                out["밑줄 속성 " + node.attr] += 1
            if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr == "path":
                out["os 사용"] -= 1
            if (
                isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr in BANNED_OS
            ):
                out["os." + node.attr] += 1
            elif (
                isinstance(node.value, ast.Name)
                and node.value.id in ("builtins", "__builtins__")
                and node.attr in BANNED_NAMES
            ):  # re.compile is fine, builtins.compile is not
                out[node.attr] += 1
        elif isinstance(node, ast.Call):
            f = node.func
            fname = (
                f.id
                if isinstance(f, ast.Name)
                else f.attr
                if isinstance(f, ast.Attribute)
                else None
            )
            if fname == "open" and _is_write_mode(node):
                out["open(쓰기)"] += 1
            elif fname in WRITE_METHODS:
                out[fname] += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            v = (
                node.value
                if isinstance(node.value, str)
                else node.value.decode("latin-1")
            )
            if "http://" in v or "https://" in v:
                out["URL 글자"] += 1
    return out


def banned_error(rel, before, after):
    b, a = danger(before), danger(after)
    added = sorted(k for k in a if a[k] > b.get(k, 0))
    return f"{rel}에 새로 넣을 수 없는 것: {', '.join(added)}" if added else None


def _changed_lines(before, after):
    return sum(
        1
        for ln in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
    )


def _diff(rel, before, after):
    return "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            f"a/{rel}",
            f"b/{rel}",
            lineterm="",
            n=2,
        )
    )


def _sig(edits):
    return _sha(
        json.dumps([[e["file"], e["old"], e["new"]] for e in edits], ensure_ascii=False)
    )


# ------------------------------------------------------------ tests
_SUMMARY = re.compile(
    r"(\b\d+ (passed|failed|errors?|skipped|deselected|xfailed|xpassed)\b.*\bin [\d.]+s)|(\bno tests ran\b)"
)


def _has_summary(text):
    """True when pytest printed its final count line (e.g. '3 failed, 9 passed, 2 errors in 4.1s')."""
    return any(_SUMMARY.search(x) for x in str(text or "").splitlines()[-5:])


def _parse_failed(text):
    out = []
    for line in str(text).splitlines():
        m = re.match(r"^(FAILED|ERROR) (.+)$", line.strip())
        if m:
            out.append(m.group(2).split(" - ")[0].strip())
    return out


REASON_CHARS = 160  # of the reason pytest prints after a failed test's name
REASON_TESTS = 3  # failed tests whose reason goes into the refusal; the rest are named only
ERROR_CHARS = 1200  # of a refusal as the operator reads it and as the trajectory keeps it: five names, three reasons
AUDIT_ERROR_CHARS = 400  # of that text on the auditor's line
_SHORT_HEAD = re.compile(r"^=+ short test summary info =+$")


def _parse_reasons(text):
    """{test id: what pytest prints after ' - ' on its FAILED/ERROR line} (the first line of the error). Read from
    pytest's own summary section alone (after its last 'short test summary info' line): a FAILED line a test printed
    earlier in the output is not pytest's word. No such section, no reasons."""
    lines = str(text).splitlines()
    heads = [i for i, x in enumerate(lines) if _SHORT_HEAD.match(x.strip())]
    out = {}
    for line in lines[heads[-1] + 1:] if heads else []:
        m = re.match(r"^(FAILED|ERROR) (.+)$", line.strip())
        if m and " - " in m.group(2):
            tid, _, why = m.group(2).partition(" - ")
            if why.strip():
                out.setdefault(tid.strip(), why.strip())
    return out


def _reason(v):
    """A failure reason as one short masked printable piece, '' when there is none."""
    if not isinstance(v, str):
        return ""
    v = c.mask("".join(ch if ch.isprintable() else " " for ch in v)).strip()
    return v if len(v) <= REASON_CHARS else v[:REASON_CHARS] + "…"


def _one_line(v, limit):
    """v as one printable line of at most limit characters."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(v)).split())[:limit]


def default_runner(stage_dir, timeout_s):
    """Run the rule code's tests in stage_dir with A2_CODE_TEST_CMD (default TEST_CMD; {root} = the live root, which is
    the stage dir minus <STAGE>/<id>/<base|new>). A run without a pytest count line is a runner error.
    -> {"ok", "failed": [test ids from FAILED/ERROR lines], "summary", "error"}, with "reasons" {test id: first
    line of its error} when tests failed. COLUMNS is set wide: at pytest's default 80 columns the reason after a long
    test name is cut away (live 11-12: two refusals in a row named the test and not why it failed)."""
    stage_dir = os.path.abspath(stage_dir)
    root = stage_dir
    for _ in range(len(STAGE.split(os.sep)) + 2):
        root = os.path.dirname(root)
    tmpl = os.environ.get(TEST_CMD_ENV) or TEST_CMD
    cmd = shlex.split(tmpl.replace("{root}", shlex.quote(root)))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", COLUMNS="400")
    try:
        p = subprocess.run(
            cmd,
            cwd=stage_dir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "failed": [],
            "summary": "",
            "error": f"시험 시간 초과 {timeout_s}초",
        }
    except OSError as e:
        return {
            "ok": False,
            "failed": [],
            "summary": "",
            "error": f"시험 실행 실패: {type(e).__name__}: {e}"[:300],
        }
    tail = [x for x in (p.stdout or "").splitlines() if x.strip()]
    summary = (tail[-1] if tail else (p.stderr or "").strip()[-300:])[:300]
    failed = _parse_failed(p.stdout)
    m = re.search(r"\b(\d+) passed\b", summary)
    passed = int(m.group(1)) if m else 0
    if not _has_summary(p.stdout):
        return {
            "ok": False,
            "failed": [],
            "summary": summary,
            "error": f"pytest 요약 줄이 없음 (종료 코드 {p.returncode}): {(p.stderr or '').strip()[-200:]}"[:300],
        }
    if p.returncode == 0:
        return {"ok": True, "failed": [], "summary": summary, "error": None, "passed": passed}
    if p.returncode == 1 and failed:
        return {"ok": False, "failed": failed, "summary": summary, "error": None, "passed": passed,
                "reasons": _parse_reasons(p.stdout)}
    if p.returncode == 1:
        return {
            "ok": False,
            "failed": [],
            "summary": summary,
            "error": "실패했으나 실패 시험 이름을 읽지 못함",
        }
    if p.returncode == 5:
        return {
            "ok": False,
            "failed": [],
            "summary": summary,
            "error": "모은 시험이 없음",
        }
    return {
        "ok": False,
        "failed": failed,
        "summary": summary,
        "error": f"pytest 종료 코드 {p.returncode}",
    }


def _dir_size(d, limit):
    """Bytes under d (no symlinks, no SKIP_DIRS), stopping once over limit."""
    total = 0
    for top, dirs, files in os.walk(d):
        dirs[:] = [n for n in dirs if n not in SKIP_DIRS and not os.path.islink(os.path.join(top, n))]
        for f in files:
            fp = os.path.join(top, f)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
        if total > limit:
            break
    return total


def _copy_rule_code(root, dst):
    """Copy what the rule code's tests need into dst: top-level *.py/*.json/*.toml/*.cfg/*.ini under TOP_FILE_MAX
    (policy.json, conftest.py, pyproject.toml), and every top-level directory (the packages the tests import) except
    SKIP_DIRS, hidden entries, symlinks and directories over DIR_MAX; inside them files over FILE_MAX are left out.
    With A2_CODE_TESTS_DIR set, tests/ is a copy of that directory instead. -> total bytes copied."""
    os.makedirs(dst, exist_ok=True)
    total = 0
    for name in sorted(os.listdir(root)):
        src = os.path.join(root, name)
        if name.startswith(".") or name in SKIP_DIRS or os.path.islink(src):
            continue
        if os.path.isfile(src):
            if name.endswith(TOP_EXT) and os.path.getsize(src) <= TOP_FILE_MAX:
                shutil.copy2(src, os.path.join(dst, name))
                total += os.path.getsize(src)
            continue
        if not os.path.isdir(src):
            continue
        if name == "tests" and os.environ.get(TESTS_DIR_ENV):
            continue  # replaced below
        if name not in ("hooks", "tests") and _dir_size(src, DIR_MAX) > DIR_MAX:
            continue  # a data directory, not a package the tests import

        def ignore(d, names):
            return [
                n
                for n in names
                if n in SKIP_DIRS
                or os.path.islink(os.path.join(d, n))
                or (
                    os.path.isfile(os.path.join(d, n))
                    and os.path.getsize(os.path.join(d, n)) > FILE_MAX
                )
            ]

        shutil.copytree(src, os.path.join(dst, name), ignore=ignore)
        for top, _, files in os.walk(os.path.join(dst, name)):
            total += sum(os.path.getsize(os.path.join(top, f)) for f in files)
        if total > STAGE_MAX_BYTES:
            raise ValueError(f"시험용 사본이 {STAGE_MAX_BYTES // 1_000_000}MB를 넘음")
    tests_dir = os.environ.get(TESTS_DIR_ENV)
    if tests_dir:
        shutil.copytree(
            tests_dir,
            os.path.join(dst, "tests"),
            ignore=shutil.ignore_patterns(*SKIP_DIRS, ".pytest_cache"),
        )
        for top, _, files in os.walk(os.path.join(dst, "tests")):
            total += sum(os.path.getsize(os.path.join(top, f)) for f in files)
    return total


def _tree_hash(d):
    h = hashlib.sha256()
    for top, dirs, files in os.walk(d):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(top, f)
            h.update(os.path.relpath(p, d).encode() + b"\0")
            with open(p, "rb") as fh:
                h.update(_sha(fh.read()).encode())
    return h.hexdigest()


def _prune_stage(root, keep=STAGE_KEEP):
    d = os.path.join(root, STAGE)
    try:
        dirs = sorted((os.path.join(d, n) for n in os.listdir(d)), key=os.path.getmtime)
    except OSError:
        return
    for p in dirs[:-keep] if keep else dirs:
        shutil.rmtree(p, ignore_errors=True)


def run_tests(root, sid, new_texts, runner):
    """Stage the rule code, run the baseline (cached by content hash) and the edited copy.
    -> {"ok": passed?, "error", "cls", "summary", "new_failed", "baseline_failed"}. The error names the newly failed
    tests (five at most) and, for the first REASON_TESTS, the reason the runner read for each."""
    tests_dir = os.environ.get(TESTS_DIR_ENV)
    if not os.path.isdir(tests_dir or os.path.join(root, "tests")):
        return {
            "ok": False,
            "error": "규칙 코드에 tests 폴더가 없어 시험할 수 없음",
            "cls": "runner",
        }
    stage = os.path.join(root, STAGE, sid)
    shutil.rmtree(stage, ignore_errors=True)
    base, new = os.path.join(stage, "base"), os.path.join(stage, "new")
    _copy_rule_code(root, base)
    shutil.copytree(base, new)
    for rel, text in new_texts.items():
        _atomic_text(os.path.join(new, *rel.split("/")), text)
    h = _tree_hash(base)
    cache = c.load_json(os.path.join(root, BASELINE), {})
    cache = cache if isinstance(cache, dict) else {}
    if cache.get("hash") == h and isinstance(cache.get("failed"), list):
        base_failed = set(cache["failed"])
        base_passed = cache.get("passed")
    else:
        r = runner(base, TEST_TIMEOUT_S)
        if not isinstance(r, dict) or r.get("error"):
            why = r.get("error") if isinstance(r, dict) else "결과 없음"
            return {
                "ok": False,
                "cls": "runner",
                "summary": r.get("summary") if isinstance(r, dict) else None,
                "error": f"고치기 전 사본의 시험이 돌지 않음: {why}",
            }
        base_failed = set(r.get("failed") or [])
        base_passed = r.get("passed")
        ex._atomic_json(
            os.path.join(root, BASELINE),
            {"hash": h, "failed": sorted(base_failed), "summary": r.get("summary"), "passed": base_passed},
            ".code_baseline.tmp-",
        )
    r = runner(new, TEST_TIMEOUT_S)
    if not isinstance(r, dict) or r.get("error"):
        why = r.get("error") if isinstance(r, dict) else "결과 없음"
        return {
            "ok": False,
            "cls": "runner",
            "summary": r.get("summary") if isinstance(r, dict) else None,
            "error": f"시험이 돌지 않음: {why}",
        }
    newly = sorted(set(r.get("failed") or []) - base_failed)
    out = {
        "ok": not newly,
        "summary": str(r.get("summary") or "")[:300],
        "new_failed": newly[:10],
        "baseline_failed": len(base_failed),
    }
    if newly:
        why = r.get("reasons") if isinstance(r.get("reasons"), dict) else {}
        # the names first, as before; then the reasons, numbered by the place of the test among those names
        whys = [f"({i}) {_reason(why.get(t))}" for i, t in enumerate(newly[:REASON_TESTS], 1) if _reason(why.get(t))]
        out.update(
            cls="tests",
            error=f"새로 실패한 시험 {len(newly)}개: {', '.join(newly[:5])}"
            + (f" | 실패 이유 (위 순서의 번호): {' '.join(whys)}" if whys else ""),
        )
    elif isinstance(base_passed, int) and isinstance(r.get("passed"), int) and r["passed"] < base_passed:
        # a test that stops being collected or gets skipped is not a FAILED line: the passed count catches it
        out.update(
            ok=False,
            cls="tests",
            error=f"통과한 시험 수가 줄어듦: {base_passed} -> {r['passed']} (건너뛰거나 모이지 않게 된 시험)",
        )
    return out


# ------------------------------------------------------------ propose
def _refuse(traj, now, cls, msg, files=(), tests=None):
    try:
        traj.append(
            "operator",
            "code_edit_refused",
            at=now,
            cls=cls,
            error=str(msg)[:ERROR_CHARS],
            files=list(files),
        )
    except Exception:  # noqa: BLE001 - the refusal itself is the answer
        pass
    return {
        "ok": False,
        "id": None,
        "error": msg,
        "tests": tests,
        "files": list(files),
        "cls": cls,
    }


def metrics_for(files):
    """Which ledger metrics judge a change: buyer for the buyer hook, shop for shop-side hooks, both for policy.py."""
    out = []
    if any(f in SHOP_FILES or f == "policy.py" for f in files):
        out.append("shop")
    if any(f in BUYER_FILES or f == "policy.py" for f in files):
        out.append("buyer")
    return out


def _snapshot(root, now, metrics, rev_weight):
    """Shop and buyer metric over the EVAL_H hours before now (for the record; the verdict recomputes)."""
    snap = {}
    try:
        t0 = now - EVAL_H * 3600
        data = ex.read_events(root, t0, now, now=now)
        if "shop" in metrics:
            m = ex.shop_metric(data, "shop:all", t0, now, rev_weight)
            snap["shop"] = {k: m[k] for k in ("value", "net", "revenue", "n")}
        if "buyer" in metrics:
            m = ex.buyer_metric(
                _buyer_all(data), "buyer:all", t0, now, ex.board_saved(root, now)
            )
            snap["buyer"] = {k: m[k] for k in ("value", "n", "others")}
    except Exception as e:  # noqa: BLE001 - a snapshot is a record, not a gate
        snap["error"] = f"{type(e).__name__}"
    return snap


def propose(
    root, edits, reason, expect, now, traj, read_files=(), runner=None, rev_weight=0.0, role=None
):
    """Gate, test and apply one code change. -> {ok, id, error, tests, files, status} (refusals also carry cls).
    role (a role operator): files outside ROLE_FILES[role] are refused first; the change keeps the role."""
    try:
        if role is not None and isinstance(edits, list):
            files = sorted({e.get("file") for e in edits if isinstance(e, dict) and isinstance(e.get("file"), str)})
            err = role_error(files, role)
            if err:
                return _refuse(traj, now, "role", err, files)
        return _propose(
            root,
            edits,
            reason,
            expect,
            now,
            traj,
            set(read_files or ()),
            runner or default_runner,
            rev_weight if _num(rev_weight) and rev_weight > 0 else 0.0,
            role,
        )
    except Exception as e:  # noqa: BLE001 - a proposal the kernel did not expect is a refusal
        return _refuse(traj, now, "error", f"{type(e).__name__}: {str(e)[:200]}")


def _propose(root, edits, reason, expect, now, traj, read_files, runner, rev_weight, role=None):
    secrets = _secrets(root)
    if (
        not isinstance(edits, list)
        or not edits
        or len(edits) > MAX_EDITS
        or not all(
            isinstance(e, dict)
            and all(isinstance(e.get(k), str) for k in ("file", "old", "new"))
            for e in edits
        )
    ):
        return _refuse(
            traj,
            now,
            "shape",
            f"edits는 {{file, old, new}} 글 객체 1~{MAX_EDITS}개의 목록이어야 함",
        )
    edits = [{"file": e["file"], "old": e["old"], "new": e["new"]} for e in edits]
    files = sorted({e["file"] for e in edits})
    reason = reason if isinstance(reason, str) else ""
    expect = expect if isinstance(expect, str) else ""
    for f in files:
        if f not in EDITABLE:
            return _refuse(
                traj,
                now,
                "not_editable",
                f"{f[:80]}는 고칠 수 없는 파일임. 고칠 수 있는 파일: {', '.join(EDITABLE)}",
                files,
            )
        if _safe(root, f) is None:
            return _refuse(
                traj,
                now,
                "not_editable",
                f"{f}를 읽을 수 없음 (없는 파일이거나 링크)",
                files,
            )
        if f not in read_files:
            return _refuse(
                traj,
                now,
                "not_read",
                f"이번 점검에서 code_read로 읽지 않은 파일은 고칠 수 없음: {f}",
                files,
            )
    if needs_restart(files) and not os.environ.get(RESTART_ENV, "").strip():
        return _refuse(
            traj,
            now,
            "restart",
            f"{', '.join(f for f in files if f in RESTART_FILES)}는 판매자 재시작이 있어야 반영되는데 재시작 명령"
            f"({RESTART_ENV})이 설정되지 않아 고칠 수 없음",
            files,
        )
    rows = load(root)
    live = [r for r in rows if _blocks(r)]
    if live:
        r = live[-1]
        return _refuse(
            traj,
            now,
            "open",
            f"코드 수정 #{r.get('id')}이 {STATUS_KO.get(r.get('status'))} 중이라 새 수정을 받지 않음",
            files,
        )
    sig = _sig(edits)
    for e in edits:
        if len(e["new"]) > NEW_CHARS:
            return _refuse(
                traj,
                now,
                "size",
                f"new는 {NEW_CHARS}자까지 ({e['file']} {len(e['new'])}자)",
                files,
            )
        if not e["old"]:
            return _refuse(
                traj, now, "old", "old가 비어 있음: 바꿀 원문을 그대로 넣음", files
            )
    before = {f: _read_text(_safe(root, f)) for f in files}
    after = dict(before)
    for i, e in enumerate(edits, 1):
        n = after[e["file"]].count(e["old"])
        if n != 1:
            why = (
                "파일에 없음 (code_read로 본 원문과 공백까지 같아야 함)"
                if n == 0
                else f"{n}번 나와 하나로 정해지지 않음"
            )
            return _refuse(
                traj, now, "old", f"수정 {i} ({e['file']})의 old가 {why}", files
            )
        after[e["file"]] = after[e["file"]].replace(e["old"], e["new"], 1)
    files = [f for f in files if after[f] != before[f]]
    if not files:
        return _refuse(
            traj, now, "noop", "바뀐 것이 없음", sorted({e["file"] for e in edits})
        )
    n_lines = sum(_changed_lines(before[f], after[f]) for f in files)
    if n_lines > MAX_CHANGED_LINES:
        return _refuse(
            traj, now, "size", f"바뀐 줄 {n_lines}줄: {MAX_CHANGED_LINES}줄까지", files
        )
    for f in files:
        try:
            ast.parse(after[f])
        except SyntaxError as e:
            return _refuse(
                traj, now, "syntax", f"{f} 문법 오류 {e.lineno}줄: {e.msg}", files
            )
        err = protected_error(f, before[f], after[f])
        if err:
            return _refuse(traj, now, "protected", err, files)
        err = banned_error(f, before[f], after[f])
        if err:
            return _refuse(traj, now, "banned", err, files)
    nid = 1 + max(
        [
            r["id"]
            for r in rows
            if isinstance(r.get("id"), int) and not isinstance(r.get("id"), bool)
        ]
        or [0]
    )
    sid = f"{nid}-{int(now)}"
    try:
        t = run_tests(root, sid, {f: after[f] for f in files}, runner)
    except Exception as e:  # noqa: BLE001 - staging trouble means the code is untested: refuse
        t = {
            "ok": False,
            "cls": "runner",
            "error": f"시험 준비 실패: {type(e).__name__}: {str(e)[:150]}",
        }
    tests = {
        k: t.get(k) for k in ("summary", "new_failed", "baseline_failed") if k in t
    }
    if not t.get("ok"):
        _prune_stage(root)
        return _refuse(
            traj,
            now,
            t.get("cls") or "tests",
            t.get("error") or "시험 통과 못함",
            files,
            tests,
        )
    shutil.rmtree(os.path.join(root, STAGE, sid), ignore_errors=True)
    _prune_stage(root)
    # apply: the live files must still be the texts that were tested against
    paths = {f: _safe(root, f) for f in files}
    if any(p is None or _read_text(p) != before[f] for f, p in paths.items()):
        return _refuse(
            traj, now, "write", "시험 중에 파일이 바뀌어 적용하지 않음", files, tests
        )
    bdir = os.path.join(root, BACKUPS, str(nid))
    written = []
    try:
        for f, p in paths.items():
            dst = os.path.join(bdir, *f.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(p, dst)
        for f, p in paths.items():
            _atomic_text(p, after[f], os.stat(p).st_mode & 0o7777)
            written.append(f)
    except OSError as e:
        for f in written:  # put back what was already written
            try:
                _atomic_text(paths[f], before[f])
            except OSError:
                pass
        return _refuse(
            traj, now, "write", f"쓰기 실패: {type(e).__name__}", files, tests
        )
    metrics = metrics_for(files)
    restart = needs_restart(files)
    diff = c.mask("\n".join(_diff(f, before[f], after[f]) for f in files), secrets)
    diff = diff if len(diff) <= DIFF_CHARS else diff[:DIFF_CHARS] + "\n[잘림]"
    ch = {
        "id": nid,
        "ts": now,
        "files": files,
        "sha_before": {f: _sha(before[f]) for f in files},
        "sha_after": {f: _sha(after[f]) for f in files},
        "reason": c.mask(reason.strip(), secrets)[:1000],
        "expect": c.mask(expect.strip(), secrets)[:500],
        "needs_restart": restart,
        "status": "restart_pending" if restart else "watching",
        "applied_ts": None if restart else now,
        "watch_until": None if restart else now + WATCH_S,
        "eval_until": None if restart else now + EVAL_H * 3600,
        "metrics": metrics,
        "rev_weight": rev_weight,
        "s0": ex.board_saved(root, now) if "buyer" in metrics else None,
        "baseline": _snapshot(root, now, metrics, rev_weight),
        "tests": tests,
        "changed_lines": n_lines,
        "sig": sig,
        "diff": diff,
        "eval_revert": False,  # the 4 h comparison only reports
    }
    if role is not None:
        ch["role"] = role
    rows.append(ch)
    _save(root, rows)
    traj.append(
        "operator",
        "code_edit",
        at=now,
        id=nid,
        files=files,
        reason=ch["reason"],
        expect=ch["expect"],
        status=ch["status"],
        metrics=metrics,
        tests=tests,
        changed_lines=n_lines,
        watch_until=ch["watch_until"],
        eval_until=ch["eval_until"],
        diff=diff,
    )
    return {
        "ok": True,
        "id": nid,
        "error": None,
        "tests": tests,
        "files": files,
        "status": ch["status"],
    }


# ------------------------------------------------------------ seller restart
def default_restarter(root, change_id, log_path):
    """Start A2_SELLER_RESTART_CMD <change id> without waiting. -> the process handle (pid, poll())."""
    cmd = shlex.split(os.environ[RESTART_ENV]) + [str(change_id)]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "ab") as log:
        return subprocess.Popen(
            cmd,
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )


def _launch(root, ch, now, restarter):
    log = os.path.join(root, "a2", "logs", f"seller_restart-{ch.get('id')}.log")
    tries = ch.get("restart_tries") if isinstance(ch.get("restart_tries"), int) else 0
    try:
        h = (restarter or default_restarter)(root, ch.get("id"), log)
        _PROCS[(root, ch.get("id"))] = h
        ch["restart"] = {
            "pid": getattr(h, "pid", None),
            "started_ts": now,
            "log": os.path.relpath(log, root),
        }
    except Exception as e:  # noqa: BLE001 - a launch that failed is a failed restart, judged on this poll
        ch["restart"] = {
            "pid": None,
            "started_ts": now,
            "launch_error": f"{type(e).__name__}: {str(e)[:150]}",
        }
    ch["restart_tries"] = tries + 1


def _restart_outcome(root, ch, now):
    """(ok, ts, why) once the current restart attempt has an outcome, None while it runs."""
    rs = ch.get("restart") or {}
    t0 = rs.get("started_ts") if _num(rs.get("started_ts")) else now
    if rs.get("launch_error"):
        return False, now, f"재시작 명령 시작 실패: {rs['launch_error']}"
    p = os.path.join(root, RESTART_RESULT)
    res = c.load_json(p, None)
    try:
        fresh = os.path.getmtime(p) >= t0 - 1
    except OSError:
        fresh = False
    if (
        isinstance(res, dict)
        and res.get("id") == ch.get("id")
        and fresh
        and (not _num(res.get("ts")) or res["ts"] >= t0 - 1)
    ):
        ts = res["ts"] if _num(res.get("ts")) else now
        return res.get("ok") is True, ts, str(res.get("why") or "")[:200]
    h = _PROCS.get((root, ch.get("id")))
    rc = None
    try:
        rc = h.poll() if h is not None else None
    except Exception:  # noqa: BLE001
        rc = None
    if rc is not None:
        return False, now, f"재시작 명령이 결과 파일 없이 끝남 (종료 코드 {rc})"
    if now - t0 >= RESTART_TIMEOUT_S:
        return False, now, f"재시작 결과가 {RESTART_TIMEOUT_S // 60}분 안에 오지 않음"
    return None


def _restore(root, ch):
    """Put the backups back when every live file still has the applied hash. -> list of files that changed since
    (nothing restored then)."""
    files = ch.get("files") or []
    paths = {f: _safe(root, f) for f in files}
    stale = []
    for f, p in paths.items():
        if p is None:
            stale.append(f)
            continue
        with open(p, "rb") as fh:
            if _sha(fh.read()) != (ch.get("sha_after") or {}).get(f):
                stale.append(f)
    if stale:
        return stale
    bdir = os.path.join(root, BACKUPS, str(ch.get("id")))
    for f, p in paths.items():
        with open(os.path.join(bdir, *f.split("/")), "rb") as fh:
            data = fh.read()
        _atomic_text(p, data, os.stat(p).st_mode & 0o7777)
    return []


def _end(ch, status, now, traj, phase, why, wake=True):
    ch.update(status=status, ended_ts=now, phase=phase, why=why)
    ch.pop("last_error", None)
    traj.append(
        "operator",
        "code_edit_end",
        at=now,
        id=ch.get("id"),
        files=ch.get("files"),
        status=status,
        phase=phase,
        why=str(why)[:300],
        verdict=ch.get("verdict"),
        **({"role": ch["role"]} if ch.get("role") in ROLE_FILES else {}),
    )
    return {
        "id": ch.get("id"),
        "status": status,
        "phase": phase,
        "why": why,
        "wake": wake,
    }


def revert(root, change, now, traj, why, phase, restarter=None):
    """Restore the backups (only when the live files still have the applied hash, else status conflict). A change
    that needed a restart goes to revert_restart_pending and its restart is started at once. Mutates change and
    returns the event."""
    stale = _restore(root, change)
    if stale:
        return _end(
            change,
            "conflict",
            now,
            traj,
            phase,
            f"{why}; 되돌리려 했으나 파일이 따로 바뀜: {', '.join(stale)}",
        )
    if change.get("needs_restart") and change.get("applied_ts") is not None:
        change.update(
            status="revert_restart_pending",
            ended_ts=now,
            phase=phase,
            why=why,
            restart_tries=0,
        )
        traj.append(
            "operator",
            "code_edit_phase",
            at=now,
            id=change.get("id"),
            status="revert_restart_pending",
            phase=phase,
            why=str(why)[:300],
        )
        _launch(root, change, now, restarter)
        return {
            "id": change.get("id"),
            "status": "revert_restart_pending",
            "phase": phase,
            "why": why,
            "wake": False,
        }
    return _end(change, "reverted", now, traj, phase, why)


def _hook_rows(root, lo, now):
    """Metrics rows of the restock, seller and buyer hooks with lo <= ts <= now."""
    out = []
    for m in c.tail_json(os.path.join(root, ".r3/metrics.jsonl"), METRICS_BYTES):
        ts = m.get("ts")
        if (
            _num(ts)
            and lo <= ts <= now
            and m.get("hook") in ("restock", "seller", "market_buyer")
        ):
            out.append(m)
    return out


def _restock_bad(m):
    o = m.get("outcome") if isinstance(m.get("outcome"), dict) else {}
    if o.get("why") == "plan_error":
        return "재입고 계획 오류(plan_error)"
    if m.get("decision") == "restock_error":
        return "재입고 훅 충돌(restock_error)"
    warns = o.get("warnings") if isinstance(o.get("warnings"), list) else []
    if any("error" in str(w).lower() for w in warns):
        return "재입고 경고에 error"
    act = (
        (m.get("extra") or {}).get("activity")
        if isinstance(m.get("extra"), dict)
        else None
    )
    if isinstance(act, dict) and act.get("error"):
        return "재입고 activity 오류"
    return None


def _is_err(m):
    o = m.get("outcome")
    return (
        "error" in str(m.get("decision") or "").lower()
        or "exception" in str(m.get("decision") or "").lower()
        or (isinstance(o, dict) and bool(o.get("error") or o.get("exception")))
    )


def _share_jump(rows, t0, hook):
    after = [m for m in rows if m.get("hook") == hook and m["ts"] >= t0]
    before = [
        m for m in rows if m.get("hook") == hook and t0 - ERR_WINDOW_S <= m["ts"] < t0
    ]
    if len(after) < ERR_MIN_ROWS:
        return None
    sa = sum(map(_is_err, after)) / len(after)
    sb = sum(map(_is_err, before)) / len(before) if before else 0.0
    if sa > max(ERR_SHARE_MIN, 2 * sb):
        return f"{hook} 오류 비율 {sa:.0%} (수정 전 1시간 {sb:.0%}, {len(after)}줄)"
    return None


def _silent(rows, t0, now, hook, gap):
    before = sum(1 for m in rows if m.get("hook") == hook and t0 - gap <= m["ts"] < t0)
    last = max(
        [m["ts"] for m in rows if m.get("hook") == hook and m["ts"] >= t0] or [t0]
    )
    if before and now - last >= gap:
        return (
            f"{hook} 기록이 {gap // 60}분 동안 없음 (수정 전 {gap // 60}분 {before}줄)"
        )
    return None


def canary(root, change, now):
    """Why the change must go back at once, or None. Checks by the hooks the changed files reach (policy.py all)."""
    t0, files = change["applied_ts"], set(change.get("files") or [])
    rows = _hook_rows(root, t0 - ERR_WINDOW_S, now)
    checks = []
    if files & {"policy.py", "hooks/restock.py"}:
        for m in rows:
            if m.get("hook") == "restock" and m["ts"] >= t0:
                bad = _restock_bad(m)
                if bad:
                    return bad
        checks.append(_silent(rows, t0, now, "restock", SILENCE_S))
    if files & {"policy.py", "hooks/seller.py"}:
        checks += [
            _share_jump(rows, t0, "seller"),
            _silent(rows, t0, now, "seller", SELLER_SILENCE_S),
        ]
    if files & {"policy.py", "hooks/market_buyer.py"}:
        checks += [
            _share_jump(rows, t0, "market_buyer"),
            _silent(rows, t0, now, "market_buyer", SILENCE_S),
        ]
    return next((x for x in checks if x), None)


def _buyer_all(data):
    """The events data with every buyer deal and other-buyer settle relabelled kind "all" (one buyer scope)."""
    return dict(
        data,
        deals=[dict(x, kind="all") for x in data.get("deals") or []],
        others=[(ts, "all") for ts, _ in data.get("others") or []],
    )


def evaluate(root, change, now):
    """Before/after over EVAL_H hours each side of the apply time (the restart time for a restarted change) with the
    ledger's sequential evaluate, no maturity wait: shop:all score proxy (net + rev_weight x revenue per hour) and/or
    the buyer saved gain per hour over both kinds. -> {"metrics", "shop"?, "buyer"?, "decision": worse | ok}."""
    t, w = change["applied_ts"], EVAL_H * 3600
    data = ex.read_events(root, t - w - ex.JOIN_LOOKBACK_H * 3600, t + w, now=now)
    out = {"metrics": list(change.get("metrics") or ["shop"])}
    for m in out["metrics"]:
        exp = {
            "side": m,
            "window_h": EVAL_H,
            "started_ts": t,
            "maturity_h": 0,
            "before_from": None,
        }
        if m == "shop":
            rw = change.get("rev_weight") or 0.0
            exp.update(
                scope="shop:all",
                rev_weight=rw,
                metric=ex.SCORE_METRIC if rw else ex.METRIC["shop"],
            )
            v = ex.evaluate(root, exp, now, data=data)
        else:
            exp.update(
                scope="buyer:all", s0=change.get("s0"), metric=ex.METRIC["buyer"]
            )
            v = ex.evaluate(root, exp, now, data=_buyer_all(data))
        out[m] = {
            k: v.get(k)
            for k in (
                "decision",
                "reason",
                "metric",
                "before",
                "before_adj",
                "after",
                "effect",
                "se",
                "n_before",
                "n_after",
                "r",
            )
        }
    out["decision"] = (
        "worse" if any(out[m]["decision"] == "worse" for m in out["metrics"]) else "ok"
    )
    return out


def _step(root, ch, now, traj, restarter):
    """Advance one open change. -> event dict or None."""
    st = ch["status"]
    if st == "restart_pending":
        if not ch.get("restart"):
            _launch(root, ch, now, restarter)
            traj.append(
                "operator",
                "code_edit_phase",
                at=now,
                id=ch.get("id"),
                status="restart_pending",
                restart=ch.get("restart"),
            )
        res = _restart_outcome(root, ch, now)
        if res is None:
            return None
        ok, ts, why = res
        _PROCS.pop((root, ch.get("id")), None)
        if ok:
            ch.update(
                status="watching",
                applied_ts=ts,
                watch_until=ts + WATCH_S,
                eval_until=ts + EVAL_H * 3600,
            )
            ch["restart"] = dict(ch["restart"], ok=True, ts=ts, why=why)
            traj.append(
                "operator",
                "code_edit_phase",
                at=now,
                id=ch.get("id"),
                status="watching",
                restart_ok=True,
            )
            return {
                "id": ch.get("id"),
                "status": "watching",
                "phase": "restart",
                "wake": False,
            }
        ch["restart"] = dict(ch["restart"], ok=False, why=why)
        stale = _restore(root, ch)
        if stale:
            return _end(
                ch,
                "conflict",
                now,
                traj,
                "restart",
                f"재시작 실패({why}), 되돌리려 했으나 파일이 따로 바뀜: {', '.join(stale)}",
            )
        return _end(
            ch,
            "refused_restart",
            now,
            traj,
            "restart",
            f"판매자 재시작 실패로 파일을 되돌림: {why}",
        )
    if st == "revert_restart_pending":
        rs = ch.get("restart") or {}
        if rs.get("ok") is False:  # the last attempt failed: try again after RETRY_S
            if _num(rs.get("failed_ts")) and now - rs["failed_ts"] < RETRY_S:
                return None
            _launch(root, ch, now, restarter)
        elif not rs:
            _launch(root, ch, now, restarter)
        res = _restart_outcome(root, ch, now)
        if res is None:
            return None
        ok, ts, why = res
        _PROCS.pop((root, ch.get("id")), None)
        if ok:
            ch["restart"] = dict(ch["restart"], ok=True, ts=ts, why=why)
            return _end(
                ch,
                "reverted",
                now,
                traj,
                ch.get("phase"),
                f"{ch.get('why')} (판매자 재시작 완료)",
            )
        ch["restart"] = dict(ch["restart"], ok=False, why=why, failed_ts=now)
        traj.append(
            "operator",
            "code_edit_phase",
            at=now,
            id=ch.get("id"),
            status="revert_restart_pending",
            restart_ok=False,
            why=why,
        )
        return {
            "id": ch.get("id"),
            "status": "revert_restart_pending",
            "phase": ch.get("phase"),
            "why": f"되돌린 뒤 판매자 재시작 실패 {ch.get('restart_tries')}회: {why}. 파일은 되돌린 상태, {RETRY_S // 60}분 뒤 다시 시도",
            "wake": True,
        }
    if st == "watching":
        why = canary(root, ch, now)
        if why:
            return revert(
                root, ch, now, traj, f"감시 중 이상: {why}", "canary", restarter
            )
        if now >= ch["watch_until"]:
            ch["status"] = "evaluating"
            traj.append(
                "operator",
                "code_edit_phase",
                at=now,
                id=ch.get("id"),
                status="evaluating",
            )
            return {
                "id": ch.get("id"),
                "status": "evaluating",
                "phase": "canary",
                "wake": False,
            }
        return None
    if now < ch["eval_until"]:
        return None
    v = evaluate(root, ch, now)
    ch["verdict"] = v
    if ch.get("eval_revert") is False:
        return _end(ch, "kept", now, traj, "evaluation", f"{EVAL_H}시간 전후 비교 숫자만 냄 (자동 되돌림 없음)")
    if v["decision"] == "worse":
        bad = [m for m in v["metrics"] if v[m]["decision"] == "worse"]
        return revert(
            root,
            ch,
            now,
            traj,
            f"{EVAL_H}시간 전후 비교에서 나빠짐 ({', '.join(bad)})",
            "evaluation",
            restarter,
        )
    return _end(ch, "kept", now, traj, "evaluation", "나빠지지 않음")


def watch(root, now, traj, restarter=None):
    """Advance every open change (restart, canary, evaluation). Never blocks (a restart is only started) and never
    raises. -> events, wake=True for the ones the operator must read (an end, a failed restart)."""
    try:
        rows = load(root)
        if not any(r.get("status") in OPEN for r in rows):
            return []
    except Exception:  # noqa: BLE001
        return []
    events, dirty = [], False
    for ch in rows:
        if ch.get("status") not in OPEN:
            continue
        try:
            if _num(ch.get("last_try_ts")) and now - ch["last_try_ts"] < RETRY_S:
                continue
            before = json.dumps(ch, sort_keys=True, default=str)
            ev = _step(root, ch, now, traj, restarter)
            dirty = dirty or json.dumps(ch, sort_keys=True, default=str) != before
            if ev:
                events.append(ev)
        except Exception as e:  # noqa: BLE001 - one bad entry must not stop the others; retried after RETRY_S
            n = ch.get("fails") if isinstance(ch.get("fails"), int) else 0
            ch.update(
                last_error=f"{type(e).__name__}: {e}"[:300],
                last_try_ts=now,
                fails=n + 1,
            )
            dirty = True
            if n + 1 >= MAX_FAILS:
                try:
                    events.append(
                        _end(
                            ch,
                            "voided",
                            now,
                            traj,
                            ch.get("status"),
                            f"판정 실패 {MAX_FAILS}회, 코드는 그대로 둠",
                        )
                    )
                except Exception:  # noqa: BLE001
                    ch.update(status="voided", ended_ts=now)
    if dirty:
        try:
            _save(root, rows)
        except Exception:  # noqa: BLE001
            pass
    return events


# ------------------------------------------------------------ state card, audit
def _verdict_text(v):
    if not isinstance(v, dict):
        return ""
    parts = []
    for m in v.get("metrics") or []:
        x = v.get(m) if isinstance(v.get(m), dict) else {}
        side = "shop" if m == "shop" else "buyer"
        parts.append(
            f"{'가게' if m == 'shop' else '구매'} 효과 {ex._num(x.get('effect'), side, sign=True)}"
            f" (se {ex._num(x.get('se'), side, False)}, {x.get('n_before', '-')}→{x.get('n_after', '-')}건)"
        )
    return ", ".join(parts)


def _hm(ts):
    return c.kst(ts, "%H:%M") if _num(ts) else "-"


def lines(root, now, role=None):
    """Up to 4 [코드] state-card lines; [] without history. With a role, the other role's lines end with ex.OTHER."""

    def mark(r):
        return ex.OTHER if role and role not in role_of(r) else ""

    try:
        rows = [r for r in load(root) if "id" in r]
        out = []
        for r in rows:
            st = r.get("status")
            if st in ("watching", "evaluating"):
                out.append(
                    f"[코드] 수정 #{r['id']} {', '.join(r.get('files') or [])} {STATUS_KO[st]} 중: "
                    f"감시 {_hm(r.get('watch_until'))}까지, "
                    + ("전후 비교 숫자 " if r.get("eval_revert") is False else "판정 ")
                    + f"{_hm(r.get('eval_until'))} "
                    f"({'/'.join(r.get('metrics') or [])} 기준)"
                    + mark(r)
                )
            elif st in ("restart_pending", "revert_restart_pending"):
                rs = r.get("restart") or {}
                out.append(
                    f"[코드] 수정 #{r['id']} {', '.join(r.get('files') or [])} {STATUS_KO[st]}: "
                    f"시도 {r.get('restart_tries') or 0}회"
                    + (
                        f", 마지막 실패 {str(rs.get('why'))[:80]}"
                        if rs.get("ok") is False
                        else ""
                    )
                    + mark(r)
                )
        ended = sorted(
            (
                r
                for r in rows
                if r.get("status") not in OPEN and _num(r.get("ended_ts"))
            ),
            key=lambda r: r["ended_ts"],
        )[-2:]
        for r in reversed(ended):
            out.append(
                f"[코드] 수정 #{r['id']} {', '.join(r.get('files') or [])} {STATUS_KO.get(r.get('status'), r.get('status'))} "
                f"{c.kst(r['ended_ts'])}: {str(r.get('why') or '')[:100]}"
                + (f", {_verdict_text(r.get('verdict'))}" if r.get("verdict") else "")
                + mark(r)
            )
        return out[:4]
    except Exception:  # noqa: BLE001
        return []


def summary(root, now, hours=24, since=None):
    """Counts from the trajectory's code rows (their `at` time) in [since or now - hours h, now]: applied, refused
    (by class), reverted at the canary or the evaluation, kept, conflict, refused_restart; the newest revert."""
    out = {
        "applied": 0,
        "refused": 0,
        "refused_by": {},
        "reverted_canary": 0,
        "reverted_eval": 0,
        "kept": 0,
        "conflict": 0,
        "refused_restart": 0,
        "last_revert": None,
    }
    try:
        start = since if _num(since) else now - hours * 3600
        for r in c.tail_json(os.path.join(root, TRAJ), 30_000_000, b'"code_edit'):
            t = r.get("at") if _num(r.get("at")) else r.get("ts")
            if not _num(t) or not start <= t <= now:
                continue
            k = r.get("kind")
            if k == "code_edit":
                out["applied"] += 1
            elif k == "code_edit_refused":
                out["refused"] += 1
                cls = str(r.get("cls") or "?")
                out["refused_by"][cls] = out["refused_by"].get(cls, 0) + 1
            elif k == "code_edit_end":
                s = r.get("status")
                if s == "reverted":
                    out[
                        "reverted_canary"
                        if r.get("phase") == "canary"
                        else "reverted_eval"
                    ] += 1
                if s in ("reverted", "refused_restart"):
                    out["last_revert"] = {
                        "id": r.get("id"),
                        "why": str(r.get("why") or "")[:160],
                    }
                if s in ("kept", "conflict", "refused_restart"):
                    out[s] += 1
    except Exception:  # noqa: BLE001
        pass
    return out


def refusal_lines(root, last=3):
    """The newest refused code edits for the auditor, one line each: time, role, files, class and what the operator
    was told (AUDIT_ERROR_CHARS). The audit numbers count refusals by class; these lines are the refusals themselves.
    The file names are what the operator wrote, so every piece is made one printable line; a row whose files are not
    a list is shown without files."""
    try:
        last = int(last)
        rows = [r for r in c.tail_json(os.path.join(root, TRAJ), 30_000_000, b'"code_edit_refused')
                if r.get("kind") == "code_edit_refused"] if last > 0 else []
        out = []
        for r in rows[-last:]:
            t = r.get("at") if _num(r.get("at")) else r.get("ts")
            files = r.get("files")
            files = [files] if isinstance(files, str) else files if isinstance(files, (list, tuple)) else []
            out.append(
                f"- 거절 {c.kst(t) if _num(t) and 1.5e9 < t < 4.1e9 else '?'} {_one_line(r.get('role') or '?', 12)} "
                f"{', '.join(_one_line(x, 60) for x in files[:4]) or '파일 없음'} [{_one_line(r.get('cls') or '?', 20)}]: "
                f"{_one_line(r.get('error') or '', AUDIT_ERROR_CHARS)}"
            )
        return out
    except Exception:  # noqa: BLE001
        return []


def history_lines(root, last=4):
    """The last changes for the auditor: time, files, reason (200), expect (160), status and numbers."""
    try:
        out = []
        for r in load(root)[-last:]:
            out.append(
                f"- #{r.get('id')} {c.kst(r['ts']) if _num(r.get('ts')) else '?'} {', '.join(r.get('files') or [])} "
                f"상태 {STATUS_KO.get(r.get('status'), r.get('status'))}"
                + (f" ({str(r.get('why'))[:120]})" if r.get("why") else "")
                + f"\n  이유: {' '.join(str(r.get('reason') or '').split())[:200]}"
                + f"\n  기대: {' '.join(str(r.get('expect') or '').split())[:160]}"
                + (
                    f"\n  숫자: {_verdict_text(r.get('verdict'))}"
                    if r.get("verdict")
                    else ""
                )
            )
        return out
    except Exception:  # noqa: BLE001
        return []
