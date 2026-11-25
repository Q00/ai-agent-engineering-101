"""Tool definitions and the one way to call them (HARNESS.md 3.1).

Every tool has a name in a namespace (me., market., memory., calc., check., act., rec., eval.),
a description that says when to use it, a JSON Schema for its input, and flags for whether it
is deterministic and whether it changes anything. call():

1. checks the input against the schema,
2. writes a tool.call event BEFORE running (week 01: the call that killed the process was the
   one that never got logged),
3. runs it and writes tool.result with the time it took,
4. returns {"ok": true, "result": ...} or {"ok": false, "reason": ..., "hint": ...}.

A tool reports a refusal by raising Refused(reason, hint); any other exception becomes
{"ok": false, "reason": "tool error: ..."} so a broken tool never takes the agent down.
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from core import bus, paths, schema
from core.causes import cause
from core.events import JOIN_KEYS, EventWriter

# C8: the version of the tool code, stamped on every call (with the strategy and card the writer already stamps)
TOOL_VERSION = hashlib.sha1(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()[:8]
SUMMARY_MAX = 300                        # C2: how much of a result the record keeps
SECRET_WORDS = ("key", "token", "secret", "password", "claim_code", "sig")


class Refused(Exception):
    def __init__(self, reason, hint=""):
        super().__init__(reason)
        self.reason, self.hint = reason, hint


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: callable
    deterministic: bool = True
    side_effect: str = ""            # empty: reads only
    latency_ms: int = 50             # target, for eval.decision_quality

    def definition(self):
        """What a model or the MCP server sees."""
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema,
                "deterministic": self.deterministic, "side_effect": self.side_effect or None}


TOOLS = {}


def tool(name, description, input_schema, **flags):
    """Decorator: register fn(ctx, **args) as a tool."""
    def deco(fn):
        TOOLS[name] = Tool(name, description, input_schema, fn, **flags)
        return fn
    return deco


@dataclass
class Ctx:
    """What a tool may look at: the kit's .market/ folder, the clock, the active strategy, the caller, and the
    decision at hand."""
    caller: str
    market_dir: Path = field(default_factory=lambda: Path(os.environ.get("MARKET_DIR") or paths.root() / ".market"))
    clock: callable = time.time
    situation: dict = field(default_factory=dict)    # the decision at hand (the act.* tools act on it)
    keys: dict = field(default_factory=dict)         # C1: the trade this call belongs to (task, order, unit, sku, ...)
    _writer: EventWriter = None
    _strategy: dict = None

    def now(self):
        return self.clock()

    @property
    def writer(self):
        if self._writer is None:
            self._writer = EventWriter(self.caller, source="tools")
        return self._writer

    @property
    def strategy(self):
        """The active strategy (or the last good one). Tools read limits from here, never from a prompt."""
        if self._strategy is None:
            self._strategy, _, _ = bus.load_validated(paths.memory() / "strategy" / "active.json", "strategy",
                                                      reader=self.caller)
        return self._strategy or {}


def clip_strings(obj, sch):
    """11/4: a text over its maxLength is cut, not refused - each refusal cost a model turn (restock's why over
    400 three times, the self analyst's summary over 1500 failed the 02:20 slice step)."""
    if isinstance(obj, str) and isinstance(sch, dict) and "maxLength" in sch and len(obj) > sch["maxLength"]:
        return obj[:sch["maxLength"] - 1] + "…"
    if isinstance(obj, dict) and isinstance(sch, dict):
        props = sch.get("properties") or {}
        return {k: clip_strings(v, props.get(k)) for k, v in obj.items()}
    if isinstance(obj, list) and isinstance(sch, dict) and isinstance(sch.get("items"), dict):
        return [clip_strings(v, sch["items"]) for v in obj]
    return obj


def summary(result):
    """C2: a short record of what a tool returned - secrets dropped, cut to SUMMARY_MAX with a mark (C9)."""
    def clean(x):
        if isinstance(x, dict):
            return {k: ("<hidden>" if any(w in str(k).lower() for w in SECRET_WORDS) else clean(v))
                    for k, v in x.items()}
        if isinstance(x, list):
            return [clean(v) for v in x[:20]]
        return x
    text = json.dumps(clean(result), ensure_ascii=False, default=str, separators=(",", ":"))
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX - 1] + "…"


def _denied(caller, name):
    """C3 (design 16-1): (why, enforce) when the caller's capability list does not hold the tool, else None. The
    check must never break a call: if the list cannot be read, the call goes on unmarked."""
    try:
        from harness import capability
        ok, why = capability.permitted(caller, name)
        return None if ok else (why, capability.ENFORCE)
    except Exception:
        return None


def call(ctx: Ctx, name: str, args=None):
    args = dict(args or {})
    t = TOOLS.get(name)
    keys = {k: v for k, v in (ctx.keys or {}).items() if k in JOIN_KEYS and v is not None}
    if t is None:
        return {"ok": False, "reason": f"unknown tool {name!r}", "hint": f"tools: {sorted(TOOLS)}"}
    given = args
    args = clip_strings(args, t.input_schema)
    errors = schema.validate(args, t.input_schema)
    if errors:
        reason = "bad input: " + "; ".join(errors[:3])
        # 11/6 11:0x: a meta proposal refused for two missing fields left no trace of what it proposed (C2)
        ctx.writer.emit("tool.result", {"tool": name, "ok": False, "ms": 0, "tool_version": TOOL_VERSION,
                                        "reason": reason, "cause": "ours", "args": args}, **keys)
        return {"ok": False, "reason": reason, "hint": "see the tool's input schema"}
    denied = _denied(ctx.caller, name)
    ctx.writer.emit("tool.call", {"tool": name, "args": args, "tool_version": TOOL_VERSION,
                                  **({"clipped": True} if args != given else {}),
                                  **({"not_permitted": denied[0]} if denied else {})}, **keys)
    if denied and denied[1]:
        out = {"ok": False, "reason": f"not permitted: {denied[0]}", "hint": "see capability.list"}
        ctx.writer.emit("tool.result", {"tool": name, "ok": False, "ms": 0, "tool_version": TOOL_VERSION,
                                        "reason": out["reason"], "cause": "limit"}, **keys)
        return out
    t0 = time.perf_counter()
    try:
        out = {"ok": True, "result": t.fn(ctx, **args)}
    except Refused as r:
        out = {"ok": False, "reason": r.reason, "hint": r.hint}
    except Exception as e:  # a broken tool must not take the agent down
        out = {"ok": False, "reason": f"tool error: {type(e).__name__}: {e}", "hint": ""}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    ctx.writer.emit("tool.result", {"tool": name, "ok": out["ok"], "ms": ms, "tool_version": TOOL_VERSION,
                                    **({"summary": summary(out["result"])} if out["ok"] else
                                       {"reason": out["reason"], "cause": cause(out["reason"])})}, **keys)
    return out


def definitions(names=None):
    return [TOOLS[n].definition() for n in (names or sorted(TOOLS))]
