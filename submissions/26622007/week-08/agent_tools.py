"""The tool registry the market agents choose from. Tools live in tools/<name>/ and are read at every call."""
import json
import os
import subprocess
import sys
import time

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


def discover(folder=None):
    """{name: manifest} of the tools that are complete. A broken one is skipped and said so, not fatal."""
    folder, out = folder or TOOLS, {}
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        path = os.path.join(folder, name)
        if not os.path.isfile(os.path.join(path, "tool.py")):
            continue
        try:
            with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
                m = json.load(f)
            out[name] = {"purpose": m["purpose"], "args": m.get("args", {})}
        except (OSError, ValueError, KeyError) as e:
            print(f"[tools] skip {name}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    return out


def run(name, args, md, agent="agent", call_id="", folder=None):
    """Runs one tool. Returns its answer, or {"error": ...} for the agent to read; every call goes to tool_calls.jsonl."""
    folder, started = folder or TOOLS, time.time()
    env = {**os.environ, "MARKET_DIR": md, "PYTHONPATH": HERE + os.pathsep + os.environ.get("PYTHONPATH", "")}
    try:
        p = subprocess.run([sys.executable, os.path.join(folder, name, "tool.py")], input=json.dumps(args), text=True,
                           capture_output=True, timeout=TOOL_TIMEOUT, env=env, cwd=HERE)
        out = json.loads(p.stdout) if p.returncode == 0 else {"error": f"exit {p.returncode}: {p.stderr[-500:]}"}
    except subprocess.TimeoutExpired:
        out = {"error": f"no answer within {TOOL_TIMEOUT} s"}
    except (OSError, ValueError) as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    text = json.dumps(out, ensure_ascii=False)
    with open(os.path.join(md, "tool_calls.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), "agent": agent, "id": call_id, "tool": name, "args": args,
                            "ok": not (isinstance(out, dict) and "error" in out), "seconds": round(time.time() - started, 2),
                            "chars": len(text)}, ensure_ascii=False) + "\n")
    return shrink(out)
