"""L3 tool maker, minimal (RL_PROACTIVE.md 2.3; LATM: propose -> verify -> wrap).

    propose   a strong model (or a person) writes the tool as one Python file with run(...) and a test file whose
              cases do not overlap the examples it was shown. forge() calls propose(feedback) up to 3 times and
              feeds the failure text back.
    verify    1. static screen: only allowlisted imports, no file writes, no process or network names, nothing
                 that touches ops/ or secrets;
              2. the tests run in a separate process: isolated interpreter (-I), empty environment (no keys),
                 a temp directory as cwd, sockets disabled, a time limit;
              3. a tool whose result is stochastic or moves the market (kind "policy") must also carry a
                 shadow_gate PASS (r3lib/shadow_gate.py on the twin); a tool that moves money needs a person's
                 name in approved_by on top of L0. A read-only tool is done after 2.
    wrap      the file goes to tools/forged/<name>.py with <name>.json beside it: description, arguments, the
              event types it serves (for the L2 dispatcher), an example call, the file's sha256 and what verified
              it. load_tools() reads the directory on the next run and hands ReAct the tools whose file still
              matches its recorded hash.

    python3 -m r3lib.forge register --name ledger_query --code a.py --tests test_a.py --description "..." \\
        --uses forum_post restock_review --arg sku=str --example '{"sku": "G1"}'
    python3 -m r3lib.forge list
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config, react

FORGED_DIR = Path(__file__).resolve().parent.parent / "tools" / "forged"
ALLOWED_IMPORTS = {"json", "math", "statistics", "time", "datetime", "re", "collections", "itertools", "functools",
                   "sqlite3", "bisect", "heapq"}
BANNED_NAMES = {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "globals", "vars", "getattr",
                "setattr", "delattr"}
BANNED_TEXT = ("ops/", ".env", ".pem", "api_key", "API_KEY", "secret", "token")
MAX_TRIES = 3
TEST_TIMEOUT_S = 30
KINDS = ("read", "policy")

RUNNER = r'''
import socket, sys, traceback
def _no_network(*a, **k):
    raise OSError("network is disabled in the forge sandbox")
socket.socket = socket.create_connection = socket.getaddrinfo = _no_network
sys.path.insert(0, ".")
import tests_tool
failed = 0
names = [n for n in dir(tests_tool) if n.startswith("test_")]
for n in names:
    try:
        getattr(tests_tool, n)()
    except Exception:
        failed += 1
        print("FAIL", n)
        traceback.print_exc(limit=3, file=sys.stdout)
print(f"{len(names) - failed} passed, {failed} failed")
sys.exit(1 if failed or not names else 0)
'''


def screen(code):
    """Reasons the tool's source may not be registered ([] when clean). The test file is not screened: it runs in
    the sandbox only and has to build its own fixtures."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    why = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            why += [f"import {m} is not allowed" for m in mods if m.split(".")[0] not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            why.append(f"{node.id} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            why.append(f"attribute {node.attr} is not allowed")
    why += [f"mentions {t!r}" for t in BANNED_TEXT if t in code]
    if code.count("connect(") != code.count("mode=ro") or ("connect(" in code and "uri=True" not in code):
        why.append("every sqlite3.connect must open the file read-only (file:...?mode=ro, uri=True)")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        why.append("the tool must define run(...)")
    return sorted(set(why))


def sandbox_test(code, tests, timeout=TEST_TIMEOUT_S):
    """(passed, output) of the tool's tests in a separate process without network, keys or our directories."""
    with tempfile.TemporaryDirectory(prefix="forge-") as d:
        Path(d, "tool.py").write_text(code, encoding="utf-8")
        Path(d, "tests_tool.py").write_text(tests, encoding="utf-8")
        Path(d, "runner.py").write_text(RUNNER, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", "runner.py"], cwd=d, env={"PATH": "/usr/bin:/bin"},
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"tests ran over {timeout} s"
        return r.returncode == 0, (r.stdout + r.stderr)[-2000:]


def forge(name, propose, description, args=None, uses=(), example=None, kind="read", moves_money=False,
          shadow_gate=None, approved_by=None, out_dir=None, max_tries=MAX_TRIES):
    """Propose -> verify -> wrap. propose(feedback) returns (code, tests); feedback is None on the first try, then
    the failure text. Returns the metadata of the registered tool, or {"registered": False, "tries": [...]}."""
    assert kind in KINDS and name.isidentifier()
    tries, feedback = [], None
    for n in range(1, max_tries + 1):
        code, tests = propose(feedback)
        why = screen(code)
        ok, out = (False, "screen: " + "; ".join(why)) if why else sandbox_test(code, tests)
        tries.append({"try": n, "ok": ok, "output": out[-600:]})
        if ok:
            break
        feedback = out
    else:
        return {"registered": False, "name": name, "tries": tries}
    meta = {"name": name, "description": description, "args": args or {}, "uses": list(uses), "example": example,
            "kind": kind, "moves_money": bool(moves_money), "sha256": hashlib.sha256(code.encode()).hexdigest(),
            "verified": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tries": len(tries),
                         "tests": tries[-1]["output"].strip().splitlines()[-1], "sandbox": "python -I, no network, "
                         "empty env, temp cwd"},
            "shadow_gate": shadow_gate, "approved_by": approved_by, "registered": True}
    meta["loadable"], meta["not_loaded_because"] = usable(meta)
    out_dir = Path(out_dir or FORGED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.py").write_text(code, encoding="utf-8")
    (out_dir / f"{name}.tests.py").write_text(tests, encoding="utf-8")       # kept so the tool can be verified again
    (out_dir / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return meta


def usable(meta):
    """(True, None) when a registered tool may be handed to an agent, else (False, why)."""
    if meta.get("kind") == "policy" and (meta.get("shadow_gate") or {}).get("verdict") != "PASS":
        return False, "a policy tool needs a shadow_gate PASS on the twin"
    if meta.get("moves_money") and not meta.get("approved_by"):
        return False, "a tool that moves money needs a person's approval"
    return True, None


def registry(out_dir=None):
    """Metadata of every registered tool whose file still matches its recorded hash."""
    out = []
    for p in sorted(Path(out_dir or FORGED_DIR).glob("*.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
            code = p.with_suffix(".py").read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        if hashlib.sha256(code.encode()).hexdigest() == meta.get("sha256") and not screen(code):
            out.append(meta)
    return out


def load_tools(db_path=None, event=None, out_dir=None):
    """react.Tool objects of the usable registered tools (those serving `event` when given). Each tool's run gets
    the path of our memory database as its first argument and must open it read-only itself."""
    out_dir = Path(out_dir or FORGED_DIR)
    db_path = str(db_path or config.DB_PATH)
    tools = []
    for meta in registry(out_dir):
        if not usable(meta)[0] or (event and event not in meta.get("uses", [])):
            continue
        spec = importlib.util.spec_from_file_location(f"forged_{meta['name']}", out_dir / f"{meta['name']}.py")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            continue
        tools.append(react.Tool(meta["name"], meta["description"],
                                (lambda run: lambda **kw: run(db_path, **kw))(mod.run), meta.get("args") or {}))
    return tools


def tools_for(event, mem=None):
    """load_tools for an agent's tool list: never raises, [] when the registry cannot be read."""
    try:
        return load_tools(getattr(mem, "path", None), event)
    except Exception:
        return []


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register")
    r.add_argument("--name", required=True)
    r.add_argument("--code", required=True)
    r.add_argument("--tests", required=True)
    r.add_argument("--description", required=True)
    r.add_argument("--uses", nargs="*", default=[])
    r.add_argument("--arg", action="append", default=[], help="name=type, repeatable")
    r.add_argument("--example")
    r.add_argument("--kind", choices=KINDS, default="read")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for m in registry():
            print(json.dumps({k: m[k] for k in ("name", "kind", "uses", "loadable", "verified")}, ensure_ascii=False))
        return 0
    code, tests = Path(a.code).read_text(encoding="utf-8"), Path(a.tests).read_text(encoding="utf-8")
    meta = forge(a.name, lambda feedback: (code, tests), a.description, kind=a.kind, uses=a.uses,
                 args=dict(x.split("=", 1) for x in a.arg), example=json.loads(a.example) if a.example else None,
                 max_tries=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return 0 if meta.get("registered") else 1


if __name__ == "__main__":
    sys.exit(main())
