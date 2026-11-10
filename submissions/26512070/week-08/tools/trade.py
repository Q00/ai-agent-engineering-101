"""The seller's move tools (user 11/10 21h): the deterministic rules as one tool, and the tools the forge made.
harness.jev picks one per turn; every move still ends through act.counter / act.accept / act.reject, so the limits
(check.price: never below cost, the list floor) hold for a forged tool exactly as for the rules.

A forged tool is a file tools/forged/<name>.py with a pure function decide(v) -> {"action", "price"} or None, where
v = {kind, list_price, cost, floor, ask, offer, turn, max_turns, known, name}. It reads nothing else and imports
nothing (harness.forge checks that and runs its test in a separate process before it is listed). Its status lives in
memory/harness/forge/registry.json: 'testing' (an A/B arm), 'adopted' (Jev may route to it), 'dropped'.
"""
import importlib.util
from pathlib import Path

from core import atomic, paths
from tools.registry import call, tool

FORGED_DIR = Path(__file__).resolve().parent / "forged"
_LOADED = {}


def registry():
    return atomic.read_json(paths.memory() / "harness" / "forge" / "registry.json", default=None) or {}


def forged(status=("adopted",), role="seller"):
    return [n for n, r in registry().items() if r.get("status") in status and r.get("role", "seller") == role]


def _load(name):
    f = FORGED_DIR / f"{name}.py"
    mt = f.stat().st_mtime
    hit = _LOADED.get(name)
    if hit and hit[0] == mt:
        return hit[1]
    spec = importlib.util.spec_from_file_location(f"forged_{name}", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _LOADED[name] = (mt, mod.decide)
    return mod.decide


def say_for(c, price, first):
    return (f"{c['name']}: {price} KRW, a premium unit with the market's certificate, its true grade and our "
            "full-refund guarantee." if first else f"For this guaranteed unit I can do {price} KRW.")


def run_forged(name, ctx, v, c, unit):
    """The forged tool's move through act.*; None when it has no move or the act refuses (the caller falls back)."""
    try:
        m = _load(name)(dict(v))
    except Exception:
        return None
    if not isinstance(m, dict) or m.get("action") not in ("counter", "accept", "reject"):
        return None
    if m["action"] == "reject":
        r = call(ctx, "act.reject", {"say": "We could not agree. Thank you for asking."})
    elif m["action"] == "accept":
        if v.get("offer") is None:
            return None
        args = {"say": f"Deal at {v['offer']} KRW. Every unit is guaranteed: the market's certificate, its true grade "
                       "and a full refund if it breaks within the warranty.", "unit": unit, "price": int(v["offer"])}
        if c.get("grade_now") in ("A", "B", "C"):
            args["claim"] = c["grade_now"]
        r = call(ctx, "act.accept", args)
    else:
        try:
            price = int(m["price"])
        except (KeyError, TypeError, ValueError):
            return None
        r = call(ctx, "act.counter", {"price": price, "say": say_for(c, price, v.get("ask") is None), "unit": unit})
    return dict(r["result"], thought=f"forged {name}") if r["ok"] else None


@tool("trade.seller_rules", "The seller's deterministic negotiation: open at playbook open_share of list (hot SKUs "
      "at hot_open_share), come down step_share of list a turn to the floor (cost x (1 + min_margin) and the list "
      "floor), accept an offer at or over our ask, or over the floor from accept_from_turn; the last turn offers the "
      "floor. Runs in about 1 ms. Returns the move, or nothing when the turn needs the agent (words only, an "
      "instruction in the text).", {"type": "object", "properties": {}}, deterministic=True)
def seller_rules(ctx):
    core, st, ex = ctx.situation["_seller"]
    return core.rule_decide(ctx, st, ex)
