"""Replay recorded situations through the seller or buyer agent (format check, no market contact).

    python3 -m kernel.replay --root <data root> --agent seller|buyer --n 10 --out <jsonl> [--dry]

Reads only the data root; writes only --out (the trajectory). --dry swaps the model for a stub that answers with the
do-nothing decision (buyer: a one-candidate plan in the buyer.plan format), so the whole path runs with no model call.
"""

import argparse
import collections
import json
import os
import re
import statistics

from kernel import common as c
from kernel import memory
from kernel import rules
from kernel.agent import Agent, parse_last_object
from kernel.trajectory import Trajectory

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET_BRIEF = os.path.join(HERE, "MARKET_BRIEF.md")
HARNESS = os.path.join(HERE, "harness")
CLI = "/home/sungsoo/.local/bin/codex"  # 11-07 오후: codex 한도 초기화로 gpt-6.1-terra 복귀 (kernel/llm.py)
MAX_UNITS = 20
MAX_REVIEWS = 10
ORDER_KEYS = (
    "acceptable",
    "budget",
    "category",
    "expires",
    "item",
    "kind",
    "order",
    "shelf_hours",
    "use_within_hours",
)
_WORD = re.compile(r"[^\W_]{2,}")


class DryTerra:
    """Stand-in model: answers with SAFE for the decision kind named in the prompt. For buyer.plan it answers with
    a format-valid plan (first shop, its first SKU, one offer of 1, max_pay 1) so the plan path is exercised."""

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        tail = prompt.split("## 지금 할 결정", 1)[-1]
        kind = next((k for k in rules.SAFE if k in tail), "")
        answer = rules.SAFE.get(kind, {})
        if kind == "buyer.plan":
            sit = parse_last_object(tail.split("## 상황", 1)[-1])
            shop = next((s for s in sit.get("shops") or [] if s.get("offers")), None)
            if shop:
                cand = {"shop": shop["id"], "sku": shop["offers"][0]["sku"], "offers": [1], "max_pay": 1}
                answer = {"candidates": [cand]}
        return "```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```"


def _tokens(text):
    return {w.lower() for w in _WORD.findall(text or "")}


def _catalog(root):
    cat = c.load_json(os.path.join(root, ".market/catalog.json"), {}) or {}
    if isinstance(cat, list):
        cat = {x.get("sku"): x for x in cat if isinstance(x, dict)}
    return cat


def seller_units(root, request_text):
    inv = c.load_json(os.path.join(root, ".market/inventory.json"), {}) or {}
    cat = _catalog(root)
    units = []
    for u in inv.get("units") or []:
        if not isinstance(u, dict):
            continue
        item = cat.get(u.get("sku")) or {}
        units.append(
            {
                "unit": u.get("unit"),
                "sku": u.get("sku"),
                "grade": u.get("grade"),
                "cost": u.get("cost"),
                "expires_at": u.get("expires_at"),
                "name": item.get("name"),
                "list_price": item.get("list_price", item.get("price")),
                "category": item.get("category"),
            }
        )
    want = _tokens(request_text)
    hit = [u for u in units if want & (_tokens(u["name"]) | _tokens(u["category"]))]
    if not hit:
        hit = sorted(units, key=lambda u: len(json.dumps(u, ensure_ascii=False)))
    return hit[:MAX_UNITS]


def seller_cases(root, n, secrets):
    path = os.path.join(root, "logs/buyer_attempts.jsonl")
    reqs = [
        r
        for r in c.tail_json(path, 50_000_000, needle=b'"request"')
        if r.get("event") == "request"
    ]
    cases = []
    for r in reqs[-n:]:
        text = r.get("text") or ""
        units = seller_units(root, text)
        cases.append(
            (
                "seller.open",
                {"request_text": c.mask(text, secrets), "units": units},
                {"units": units},
            )
        )
    return cases


def buyer_case(root, login):
    order = c.load_json(os.path.join(root, ".market/order.json"), {}) or {}
    shops = c.load_json(os.path.join(root, ".market/shops.json"), []) or []
    return buyer_situation(order, shops, _catalog(root), login)


def buyer_situation(order, shops, catalog, login):
    """(kind, situation, ctx) for one order. Our own shop is dropped; shops get short ids s1..sN (urls stay in ctx).
    Each offer carries the catalog list price (the buyer's value of a good unit)."""
    sit_shops, ctx_shops, ids = [], [], {}
    for s in shops:
        if not isinstance(s, dict) or not s.get("url") or c.is_mine(s, login):
            continue
        sid = f"s{len(ids) + 1}"
        ids[sid] = s["url"]
        offers = [
            {k: o.get(k) for k in ("sku", "name", "qty")}
            for o in s.get("offers") or []
            if isinstance(o, dict)
        ]
        for o in offers:
            item = catalog.get(o["sku"]) if isinstance(o["sku"], str) else None
            item = item if isinstance(item, dict) else {}
            o["list_price"] = item.get("list_price", item.get("price"))
        sit_shops.append(
            {
                "id": sid,
                "name": c.hid(s.get("name") or s["url"]),
                "offers": offers,
                "reviews": c.scrub((s.get("reviews") or [])[-MAX_REVIEWS:], login),
            }
        )
        ctx_shops.append({"url": s["url"], "offers": offers})
    situation = {
        "order": {k: order[k] for k in ORDER_KEYS if k in order},
        "shops": sit_shops,
    }
    return (
        "buyer.plan",
        situation,
        {"budget": order.get("budget"), "shops": ctx_shops, "shop_ids": ids},
    )


def _action(kind, decision):
    if kind == "buyer.plan":
        return f"candidates={len(decision.get('candidates') or [])}"
    return decision.get("action", "?")


def _error_kind(e):
    """'model: ModelRefused: ...' -> 'model: ModelRefused'; a rule error -> 'model: rule'."""
    parts = e.split(": ")
    if parts[0] == "harness":
        return "harness"
    if len(parts) >= 3 and re.fullmatch(r"[A-Z]\w+", parts[1]):
        return f"{parts[0]}: {parts[1]}"
    return f"{parts[0]}: rule"


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def run(root, agent_name, n, out, dry=False, cli=CLI, harness_dir=None, isolate=True):
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    traj = Trajectory(out, secrets=secrets)
    if dry:
        terra = DryTerra()
    else:
        from kernel.llm import Terra

        terra = Terra(traj, cli=cli, secrets=secrets)
    agent = Agent(
        agent_name,
        harness_dir or os.path.join(HARNESS, agent_name),
        terra,
        traj,
        MARKET_BRIEF,
        isolate=isolate,
        mem_sock=memory.sock_path(root),
    )
    if agent_name == "seller":
        cases = seller_cases(root, n, secrets)
    else:
        cases = [buyer_case(root, login)] * max(1, n)
    routes, actions, errs, lats, valid = (
        collections.Counter(),
        collections.Counter(),
        collections.Counter(),
        [],
        0,
    )
    for kind, situation, ctx in cases:
        decision, meta = agent.decide(kind, situation, ctx)
        routes[meta["route"]] += 1
        actions[_action(kind, decision)] += 1
        lats.append(meta["latency_s"])
        valid += not meta["errors"]
        for e in meta["errors"]:
            errs[_error_kind(e)] += 1
    summary = {
        "agent": agent_name,
        "n": len(cases),
        "valid": valid,
        "routes": dict(routes),
        "actions": dict(actions),
        "latency_s": {
            "median": statistics.median(lats) if lats else None,
            "p90": _pct(lats, 0.9),
            "max": max(lats) if lats else None,
        },
        "error_kinds": dict(errs),
    }
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--agent", choices=("seller", "buyer"), required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--cli", default=CLI)
    ap.add_argument("--harness", default=None)
    a = ap.parse_args(argv)
    summary = run(
        c.data_root(a.root),
        a.agent,
        a.n,
        a.out,
        dry=a.dry,
        cli=a.cli,
        harness_dir=a.harness,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
