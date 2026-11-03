"""The tool registry the market agents choose from. Tools live in tools/<name>/ and are read at every call.

A tool runs in a box (hard_limits.SANDBOX, macOS sandbox-exec): no network, no writes but a fresh folder for the call
(.market/ is read only), no reading the home folder beyond the repository and Python, nor keys or .env files, no view of
other processes, no program but Python, and an environment without keys. Without sandbox-exec a tool does not run. So a tool the agents or the
meta-harness write can read the market's files and compute, but cannot send anything, spend anything or see a secret.
A tool that fails its selftest (manifest "selftest": {"args": ...}, run once per version of tool.py) or most of its
recent calls is left out of discover() until it is fixed; .market/tools_status.json says which and why.
"""
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import hard_limits

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.environ.get("AGENT_TOOLS") or os.path.join(HERE, "tools")
TOOL_TIMEOUT = 30
MAX_RESULT = 8000     # characters of a tool's answer that reach the prompt


def shrink(out, limit=MAX_RESULT):
    """The answer as JSON text of at most limit characters, always valid JSON: the longest lists are halved until it fits,
    and "_cut" says how much went. Cut text would leave the agent reading half a record."""
    text = json.dumps(out, ensure_ascii=False)
    if len(text) <= limit:
        return text
    wrapped = out if isinstance(out, dict) else {"items": out}
    wrapped, cut = json.loads(json.dumps(wrapped)), 0

    def lists(node):
        if isinstance(node, list):
            yield node
        for child in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()):
            yield from lists(child)

    def maps(node):
        if isinstance(node, dict):
            yield node
        for child in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()):
            yield from maps(child)

    while len(json.dumps({**wrapped, "_cut": cut}, ensure_ascii=False)) > limit:
        seqs = [x for x in lists(wrapped) if len(x) > 1] + [x for x in maps(wrapped) if len(x) > 1]
        if not seqs:
            return json.dumps({"_cut": "all", "preview": text[:max(0, limit - 40)]}, ensure_ascii=False)
        big = max(seqs, key=lambda x: len(json.dumps(x, ensure_ascii=False)))
        keep = len(big) // 2
        if isinstance(big, list):
            cut += len(big) - keep
            del big[keep:]
        else:
            for k in list(big)[keep:]:
                del big[k]
                cut += 1
    return json.dumps({**wrapped, "_cut": cut}, ensure_ascii=False)


def tool_cfg():
    try:
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            return {"disable_fail_share": 0.6, "disable_min_calls": 5, **(json.load(f).get("tools") or {})}
    except (OSError, ValueError):
        return {"disable_fail_share": 0.6, "disable_min_calls": 5}


def failing(md, cfg=None, last=20):
    """{tool: why} for tools most of whose recent calls failed."""
    cfg, calls = cfg or tool_cfg(), collections.defaultdict(list)
    try:
        with open(os.path.join(md, "tool_calls.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    c = json.loads(line)
                except ValueError:
                    continue
                calls[c.get("tool")].append(bool(c.get("ok")))
    except OSError:
        return {}
    out = {}
    for name, oks in calls.items():
        oks = oks[-last:]
        bad = oks.count(False)
        if len(oks) >= cfg["disable_min_calls"] and bad / len(oks) >= cfg["disable_fail_share"]:
            out[name] = f"{bad} of its last {len(oks)} calls failed"
    return out


def selftest(name, m, md, folder=None):
    """None if the tool passes its selftest (or has none), else why. Remembered per version of tool.py."""
    folder = folder or TOOLS
    test = m.get("selftest")
    if not isinstance(test, dict):
        return None
    with open(os.path.join(folder, name, "tool.py"), "rb") as f:
        version = hashlib.sha256(f.read() + json.dumps(test, sort_keys=True).encode()).hexdigest()[:16]
    path = os.path.join(md, "tool_selftest.json")
    try:
        with open(path, encoding="utf-8") as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    if seen.get(name, {}).get("version") != version:
        out = execute(name, test.get("args") or {}, md, folder)
        why = out["error"] if isinstance(out, dict) and "error" in out else None
        seen[name] = {"version": version, "why": why, "at": int(time.time())}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False)
    return seen[name]["why"]


def discover(folder=None, md=None):
    """{name: manifest} of the tools that are complete and working. A broken one is skipped and said so, not fatal."""
    folder, out = folder or TOOLS, {}
    md = md or os.environ.get("MARKET_DIR")
    bad = failing(md) if md else {}
    status = {}
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        path = os.path.join(folder, name)
        if not os.path.isfile(os.path.join(path, "tool.py")):
            continue
        try:
            with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
                m = json.load(f)
            why = bad.get(name) or (selftest(name, m, md, folder) if md else None)
            if why:
                status[name] = why
                print(f"[tools] left out {name}: {why}", file=sys.stderr, flush=True)
                continue
            out[name] = {"purpose": m["purpose"], "args": m.get("args", {})}
        except (OSError, ValueError, KeyError) as e:
            status[name] = f"{type(e).__name__}: {e}"
            print(f"[tools] skip {name}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    if md:
        try:
            with open(os.path.join(md, "tools_status.json"), "w", encoding="utf-8") as f:
                json.dump({"at": int(time.time()), "left_out": status, "available": sorted(out)}, f, ensure_ascii=False)
        except OSError:
            pass
    return out


def boxed(cmd, md, work):
    """cmd inside the sandbox, with an environment that holds no keys. None when there is no sandbox here: a tool does
    not run unboxed."""
    sandbox = shutil.which("sandbox-exec")
    if not sandbox:
        return None, None
    home = os.path.realpath(os.path.expanduser("~"))
    real = lambda p: os.path.realpath(p)
    repo = real(os.path.join(HERE, "..", "..", ".."))
    reads = {repo, real(HERE), real(md), real(work), real(sys.prefix), real(sys.base_prefix), real(tempfile.gettempdir()),
             real(os.path.dirname(real(sys.executable)))}
    if os.environ.get("AGENT_MEMORY_DIR"):
        reads.add(real(os.environ["AGENT_MEMORY_DIR"]))
    q = lambda p: '"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"'
    profile = hard_limits.SANDBOX.format(
        work=real(work), home=home,
        exe=" ".join(f"(literal {q(p)})" for p in {sys.executable, real(sys.executable)}),
        reads=" ".join(f"(subpath {q(p)})" for p in sorted(reads)))
    env = {k: v for k, v in os.environ.items()
           if k in hard_limits.ENV_KEEP or (k.startswith(hard_limits.ENV_PASS_PREFIX) and not re.search(hard_limits.ENV_SECRET, k))}
    env.update(MARKET_DIR=md, PYTHONPATH=HERE, PYTHONDONTWRITEBYTECODE="1", TMPDIR=work)
    return [sandbox, "-p", profile, *cmd], env


def execute(name, args, md, folder=None):
    folder = folder or TOOLS
    with tempfile.TemporaryDirectory(prefix="tool-") as work:
        cmd, env = boxed([sys.executable, os.path.join(folder, name, "tool.py")], md, work)
        if cmd is None:
            return {"error": "no sandbox-exec here: tools do not run without the sandbox"}
        try:
            p = subprocess.run(cmd, input=json.dumps(args), text=True, capture_output=True, timeout=TOOL_TIMEOUT, env=env, cwd=HERE)
            return json.loads(p.stdout) if p.returncode == 0 else {"error": f"exit {p.returncode}: {p.stderr[-500:]}"}
        except subprocess.TimeoutExpired:
            return {"error": f"no answer within {TOOL_TIMEOUT} s"}
        except (OSError, ValueError) as e:
            return {"error": f"{type(e).__name__}: {e}"}


def run(name, args, md, agent="agent", call_id="", folder=None):
    """Runs one tool. Returns its answer, or {"error": ...} for the agent to read; every call goes to tool_calls.jsonl."""
    folder, started = folder or TOOLS, time.time()
    out = execute(name, args, md, folder)
    text = json.dumps(out, ensure_ascii=False)
    with open(os.path.join(md, "tool_calls.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), "agent": agent, "id": call_id, "tool": name, "args": args,
                            "ok": not (isinstance(out, dict) and "error" in out), "seconds": round(time.time() - started, 2),
                            "chars": len(text)}, ensure_ascii=False) + "\n")
    return shrink(out)
