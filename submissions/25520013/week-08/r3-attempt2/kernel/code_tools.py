"""Read-only code tools (human code, not editable by the model): a short Python function the agent writes and runs now.

The agent sends `def run(load, args): ...`. check() is a static AST gate; execute() runs the code in a separate
process (sys.executable -I -B, empty environment, fresh temp cwd, resource limits, wall-clock timeout that kills the
process group). In the child, the code gets a restricted __builtins__ (an __import__ that hands out only the public
non-module names of the allowed modules) and load(source, hours, where), which returns log rows with identity values
hashed (our own marked 우리) and owner keys dropped. The child imports only the stdlib, kernel.common and
kernel.made_tools; kernel.code_edit is imported by the parent-side functions only.
"""

import ast
import json
import math
import os
import re
import resource
import signal
import shutil
import subprocess
import sys
import tempfile
import traceback
import types

from kernel import common as c
from kernel import made_tools as mt

TIMEOUT_S = 20  # wall clock per run
MEM_BYTES = 1_000_000_000  # RLIMIT_AS (skipped where the OS refuses it, e.g. macOS)
NOFILE = 32
CODE_CHARS = 6_000
OUT_CHARS = 6_000  # one observation, the compact operator's COMPACT_OBS
STR_CHARS = 300  # per string value in a loaded row (log text is untrusted)
LOAD_ROWS = 200_000  # rows one load() may return
LOAD_BYTES = {"events": 200_000_000}  # tail read per load(); 24 h of events is large
MAX_HOURS = 48
ERR = "코드 오류:"
DROP_KEYS = ("owner", "own_owner")
EXTRA_BANNED = ("open",)
# Attributes that reach frames, code objects or interpreter modules from an ordinary object (generator, traceback).
BANNED_ATTRS = (
    "gi_frame",
    "gi_code",
    "cr_frame",
    "cr_code",
    "ag_frame",
    "ag_code",
    "tb_frame",
    "tb_next",
    "f_back",
    "f_globals",
    "f_locals",
    "f_builtins",
    "f_code",
    "modules",
    "sys",
    "os",
    "builtins",
)
SAFE_BUILTINS = (
    "len",
    "range",
    "sum",
    "min",
    "max",
    "sorted",
    "enumerate",
    "zip",
    "map",
    "filter",
    "any",
    "all",
    "abs",
    "round",
    "int",
    "float",
    "str",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "isinstance",
    "reversed",
    "next",
    "iter",
    "divmod",
    "pow",
    "repr",
    "ValueError",
    "KeyError",
    "TypeError",
    "ZeroDivisionError",
    "IndexError",
    "Exception",
)
FILENAME = "<analyze>"

_FIELD = re.compile(
    r"\{[^{}:!]*[.\[]"
)  # str.format field with attribute or index access, e.g. "{0.gi_frame}"
_CTRL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]+")
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHILD = f"import sys; sys.path.insert(0, {_BASE!r}); from kernel import code_tools; code_tools._child()"


# ------------------------------------------------------------ parent side
def check(code):
    """Static gate of an analysis code. Returns a Korean error string or None."""
    from kernel import (
        code_edit,
    )  # parent only: the child imports this module and must not load code_edit

    if not isinstance(code, str) or not code.strip():
        return "code는 def run(load, args): 를 담은 파이썬 글이어야 함"
    if len(code) > CODE_CHARS:
        return f"코드가 너무 김: {len(code)}자 > {CODE_CHARS}"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"문법 오류 {e.lineno}번째 줄: {e.msg}"
    bad = [k for k, v in code_edit.danger(code).items() if v > 0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in EXTRA_BANNED:
            bad.append(node.id)
        elif isinstance(node, ast.Name) and node.id.startswith("_") and node.id != "_":
            bad.append("밑줄 이름 " + node.id)
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            bad.append("밑줄 속성 " + node.attr)
        elif isinstance(node, ast.Attribute) and node.attr in BANNED_ATTRS:
            bad.append("속성 " + node.attr)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bad.append("global/nonlocal")
        elif isinstance(node, ast.ClassDef):
            bad.append("class 정의")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _FIELD.search(node.value)
        ):
            bad.append("format 필드 속성 접근")
    if bad:
        return "쓸 수 없는 것: " + ", ".join(sorted(set(bad)))
    runs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
    if len(runs) != 1:
        return f"맨 바깥에 def run(load, args): 가 하나 있어야 함 (지금 {len(runs)}개)"
    a = runs[0].args
    if len(a.posonlyargs) + len(a.args) != 2 or a.vararg or a.kwarg or a.kwonlyargs:
        return "run의 인자는 (load, args) 두 개여야 함"
    return None


def child_env():
    """Environment of the child process: empty, nothing of the parent's (keys, tokens) reaches it."""
    return {}


def _limits(cpu_s):
    """Resource limits applied in the child before exec. A limit the OS refuses is skipped (RLIMIT_AS on macOS)."""
    for res, val in (
        (resource.RLIMIT_CPU, cpu_s),
        (resource.RLIMIT_AS, MEM_BYTES),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, NOFILE),
        (resource.RLIMIT_FSIZE, 0),
    ):
        try:
            hard = resource.getrlimit(res)[1]
            val = val if hard == resource.RLIM_INFINITY else min(val, hard)
            resource.setrlimit(res, (val, val))
        except (ValueError, OSError):
            pass


def _kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    proc.communicate()


def _error_text(res, stderr, rc):
    if isinstance(res, dict):
        if res.get("type") == "MemoryError":
            return f"{ERR} 메모리 한도(약 {MEM_BYTES // 1_000_000_000}GB)를 넘음. load의 where나 hours로 좁힐 것"
        where = f" {res['line']}번째 줄" if res.get("line") else ""
        return f"{ERR} {res.get('type')}{where}: {str(res.get('msg'))[:500]}"
    if rc == -signal.SIGXCPU or rc == -signal.SIGKILL:
        return (
            f"{ERR} CPU 시간 한도를 넘어 멈춤. 반복을 줄이거나 where, hours로 좁힐 것"
        )
    last = (stderr.decode("utf-8", "replace").strip().splitlines() or ["출력 없음"])[-1]
    return f"{ERR} 실행이 비정상으로 끝남 (종료 코드 {rc}): {last[:300]}"


def execute(root, code, args=None, now=None, timeout_s=TIMEOUT_S):
    """Run def run(load, args) in a sandboxed child over the logs of root. -> the masked result text (OUT_CHARS max)
    or a Korean error starting with 코드 오류:."""
    from kernel import code_edit  # parent only, see check()

    err = check(code)
    if err:
        return f"{ERR} {err}"
    if args is not None and not isinstance(args, dict):
        return f"{ERR} args는 객체(dict)여야 함"
    root = os.path.abspath(root)
    login = c.owner_login(root)
    req = json.dumps(
        {
            "root": root,
            "code": code,
            "args": args or {},
            "now": c.now_ts() if now is None else now,
            "allowed": list(code_edit.ALLOWED_IMPORTS),
        },
        ensure_ascii=False,
        default=str,
    )
    with tempfile.TemporaryDirectory(prefix="a2_code_") as tmp:
        proc = subprocess.Popen(
            _netns() + [sys.executable, "-I", "-B", "-c", _CHILD],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmp,
            env=child_env(),
            start_new_session=True,
            preexec_fn=lambda: _limits(math.ceil(timeout_s) + 1),
        )
        try:
            out, stderr = proc.communicate(req.encode("utf-8"), timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill(proc)
            return f"{ERR} 시간 한도 {timeout_s}초를 넘어 멈춤 (끝나지 않는 반복이거나 읽기가 너무 큼. where, hours로 좁힐 것)"
    lines = out.decode("utf-8", "replace").strip().splitlines()
    try:
        res = json.loads(lines[-1]) if lines else None
    except ValueError:
        res = None
    if not isinstance(res, dict) or not res.get("ok"):
        return c.mask(
            _CTRL.sub(" ", _error_text(res, stderr, proc.returncode)), (login,)
        )
    text = "\n".join(
        [f"[주의: {n}. 결과가 불완전함]" for n in res.get("notes") or []]
        + [str(res.get("text"))]
    )
    text = c.mask(_CTRL.sub(" ", text), (login,))
    total = len(text) + max(0, int(res.get("n") or 0) - len(str(res.get("text"))))
    if total <= OUT_CHARS:
        return text
    keep = OUT_CHARS - 100
    return (
        text[:keep]
        + f"\n[잘림: 결과 {total:,}자 중 {keep:,}자만 보임. run이 더 짧게 요약해 돌려줄 것]"
    )


# ------------------------------------------------------------ child side
def _mark(v, login):
    """An identity value as made_tools._cell shows it: 우리 for our own login, else a stable hash."""
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return "우리" if login and login in s.lower() else c.hid(s)


_NETNS = None


def _netns():
    """["unshare", "-rn"] where the OS lets an ordinary user start a process without a network (Linux user
    namespaces; probed once), else []: a Python-level escape then still has no network to reach."""
    global _NETNS
    if _NETNS is None:
        _NETNS = []
        exe = shutil.which("unshare")
        if exe:
            try:
                if subprocess.run([exe, "-rn", "true"], capture_output=True, timeout=5, env={}).returncode == 0:
                    _NETNS = [exe, "-rn"]
            except (OSError, subprocess.SubprocessError):
                pass
    return list(_NETNS)


def clean_row(v, login, hashed=False):
    """Copy of a loaded row: values under identity keys (made_tools.ID_KEYS, any depth) hashed, owner keys dropped,
    long strings cut to STR_CHARS."""
    if isinstance(v, dict):
        return {
            k: clean_row(x, login, hashed or str(k).lower() in mt.ID_KEYS)
            for k, x in v.items()
            if str(k).lower() not in DROP_KEYS
        }
    if isinstance(v, list):
        return [clean_row(x, login, hashed) for x in v]
    if hashed and v is not None and v != "":
        return _mark(v, login)
    if isinstance(v, str) and len(v) > STR_CHARS:
        return v[:STR_CHARS]
    return v


def make_load(root, now, login, notes):
    """The load(source, hours=24, where=None) handed to the code. Coverage gaps are appended to notes."""

    def load(source, hours=24, where=None):
        if source not in mt.SOURCES:
            raise ValueError(f"source는 {', '.join(mt.SOURCES)} 중 하나")
        if (
            isinstance(hours, bool)
            or not isinstance(hours, (int, float))
            or not 0 < hours <= MAX_HOURS
        ):
            raise ValueError(f"hours는 0..{MAX_HOURS} 숫자")
        if where is not None and (not isinstance(where, str) or not where):
            raise ValueError("where는 줄에 들어 있을 글자 (문자열)")
        path = os.path.join(root, mt.SOURCES[source])
        cap = LOAD_BYTES.get(source, mt.MAX_BYTES)
        lo = now - hours * 3600
        out, first_ts = [], None
        for row in c.tail_json(path, cap, where.encode("utf-8") if where else None):
            ts = row.get("ts")
            if not mt._num(ts):
                continue
            if first_ts is None:
                first_ts = ts
            if not lo <= ts <= now:
                continue
            if len(out) >= LOAD_ROWS:
                raise ValueError(
                    f"{source} 행 한도 {LOAD_ROWS:,} 넘음: where나 hours로 좁힐 것"
                )
            d = row.get("data")
            if isinstance(d, dict) and "own_owner" in d:  # the owner flag is dropped; keep which side we were on
                row = dict(row, data=dict(d, side=c.settle_side(d, login)))
            out.append(clean_row(row, login))
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > cap and first_ts is not None and first_ts > lo:
            notes.append(
                f"{source} 읽기 한도 {cap // 1_000_000}MB가 {hours}시간 창 전체를 덮지 못함"
            )
        return out

    return load


def _guarded_import(allowed):
    cache = {}

    def imp(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in allowed:
            raise ImportError(f"import 불가: {name} (허용: {', '.join(allowed)})")
        if name not in cache:
            mod = __import__(name)
            cache[name] = types.SimpleNamespace(
                **{
                    k: v
                    for k, v in vars(mod).items()
                    if not k.startswith("_") and not isinstance(v, types.ModuleType)
                }
            )
        return cache[name]

    return imp


def safe_builtins(allowed):
    import builtins

    out = {k: getattr(builtins, k) for k in SAFE_BUILTINS}
    out["print"] = lambda *a, **k: None
    out["__import__"] = _guarded_import(tuple(allowed))
    return out


def _render(res):
    if isinstance(res, str):
        return res
    try:
        return json.dumps(res, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(res)


def _child():
    """Child entry: request JSON on stdin, one result JSON line on stdout."""
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        notes = []
        load = make_load(req["root"], req["now"], c.owner_login(req["root"]), notes)
        g = {"__builtins__": safe_builtins(req["allowed"]), "__name__": "analyze"}
        exec(compile(req["code"], FILENAME, "exec"), g)
        text = _render(g["run"](load, req["args"]))
        out = {
            "ok": True,
            "text": text[: 2 * OUT_CHARS],
            "n": len(text),
            "notes": notes,
        }
    except (
        BaseException
    ) as e:  # every failure of the code is an answer to the agent, not a crash
        line = next(
            (
                f.lineno
                for f in reversed(traceback.extract_tb(e.__traceback__))
                if f.filename == FILENAME
            ),
            None,
        )
        out = {"ok": False, "type": type(e).__name__, "msg": str(e)[:500], "line": line}
    sys.stdout.write(json.dumps(out, default=str) + "\n")
    sys.stdout.flush()
