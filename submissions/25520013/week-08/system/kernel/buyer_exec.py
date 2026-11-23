"""Mechanical execution of a validated buyer.plan over A2A (human code, not editable by the model).

    python3 -m kernel.buyer_exec --shops {shops} --item {item} --budget {budget} --order {order}

Same CLI and last output line as the rule-code market_buyer hook: {"shop", "receipt", "stars", "review"}. The model
is called once per order (buyer.plan); this module only carries the plan out. For each candidate in plan order it opens
a negotiation, accepts as soon as the shop's ask is <= max_pay, otherwise sends the plan's offers in order, and walks
(CancelTask) when they run out. It stops at the first receipt that passes the checks. No pricing logic here: every
number sent comes from the plan, or is the shop's own ask when that ask is within max_pay.

The network is the injected `transport(url, body, timeout) -> dict` (body None means GET). Stdlib only.
"""

import argparse
import base64
import json
import os
import re
import time
import urllib.request
import uuid

from kernel import common as c

INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
COMPLETED = "TASK_STATE_COMPLETED"
DEADLINE_S = (
    200  # the kit stops the buyer at 300 s; the plan call runs inside this budget too
)
RPC_TIMEOUT_S = 60
CARD_TIMEOUT_S = 20
CANCEL_TIMEOUT_S = 10
RPC_FLOOR_S = 3
RECEIPT_SIGNED = ("shop", "item", "price", "task")


class Timeout(Exception):
    """A request ran past its timeout."""


def http_json(url, body=None, timeout=RPC_TIMEOUT_S):
    """Default transport: GET (body None) or JSON POST with the A2A version header, parsed JSON back."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"A2A-Version": "1.0", "Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except TimeoutError as e:
        raise Timeout(str(e)) from e
    except OSError as e:
        if isinstance(getattr(e, "reason", None), TimeoutError):
            raise Timeout(str(e)) from e
        raise


def rpc(transport, url, text, task_id=None, timeout=RPC_TIMEOUT_S):
    msg = {
        "role": "ROLE_USER",
        "messageId": str(uuid.uuid4()),
        "parts": [{"text": text}],
    }
    if task_id:
        msg["taskId"] = task_id
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4())[:8],
        "method": "SendMessage",
        "params": {"message": msg},
    }
    r = transport(url, body, timeout)
    if not isinstance(r, dict) or "error" in r or not isinstance(r.get("result"), dict):
        return None
    return r["result"].get("task", r["result"])


def cancel(transport, url, task_id):
    """Tell the shop we left so it can free the unit it held for us (A2A CancelTask). Best effort."""
    if not task_id:
        return
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4())[:8],
        "method": "CancelTask",
        "params": {"id": task_id},
    }
    try:
        transport(url, body, CANCEL_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - best effort, the result is already decided
        pass


def endpoint(transport, shop_url, timeout=CARD_TIMEOUT_S):
    card = transport(
        shop_url.rstrip("/") + "/.well-known/agent-card.json", None, timeout
    )
    card = card if isinstance(card, dict) else {}
    ifs = card.get("supportedInterfaces") or []
    return (
        ifs[0].get("url") if ifs and isinstance(ifs[0], dict) else None
    ) or card.get("url")


def state(t):
    return ((t or {}).get("status") or {}).get("state", "")


def text_of(t):
    parts = (((t or {}).get("status") or {}).get("message") or {}).get("parts") or []
    return " ".join(p.get("text", "") for p in parts if isinstance(p, dict))


def ask_of(t):
    s = re.sub(r"\(G[0-9A-Za-z]{5,}\)", " ", text_of(t))
    m = re.findall(r"asking (\d+)|Counteroffer: (\d+)", s)
    if m:
        return int(next(x for x in m[-1] if x))
    return None


def receipt_of(t):
    for a in (t or {}).get("artifacts") or []:
        for p in (a or {}).get("parts") or []:
            d = p.get("data") if isinstance(p, dict) else None
            if isinstance(d, dict) and "price" in d and d.get("sig_seller"):
                return d
    return None


def verify_receipt(rec, pub_b64):
    """(ok, receipt, how) as in the rule hooks: ok None when there is no key or no ed25519 library (stdlib only), False
    when the signature does not match. An integral float price is retried as int, the bytes the seller signed."""
    if not pub_b64 or not isinstance(rec, dict) or not rec.get("sig_seller"):
        return None, rec, "no_key" if not pub_b64 else "no_sig"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return None, rec, "no_crypto"
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        sig = base64.b64decode(rec["sig_seller"])
    except (ValueError, TypeError):
        return False, rec, "bad_key_or_sig"

    def ok(r):
        payload = json.dumps(
            {k: r.get(k) for k in RECEIPT_SIGNED},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        try:
            key.verify(sig, payload.encode())
            return True
        except InvalidSignature:
            return False

    if ok(rec):
        return True, rec, "as_received"
    p = rec.get("price")
    if isinstance(p, float) and p.is_integer():
        r2 = dict(rec, price=int(p))
        if ok(r2):
            return True, r2, "int_normalised"
    return False, rec, "mismatch"


def receipt_price(rec):
    """Receipt price as int (an integral float counts), else None."""
    p = rec.get("price")
    if isinstance(p, bool):
        return None
    if isinstance(p, float) and p.is_integer():
        p = int(p)
    return p if isinstance(p, int) else None


def haggle(cand, name, budget, deadline, transport, pubkey=None, clock=time.time):
    """One candidate -> (receipt or None, info). info: offers_sent, final_ask, result, why."""
    cap = min(cand["max_pay"], budget)
    offers = iter(cand["offers"])
    info = {"offers_sent": [], "final_ask": None, "result": None}
    url, t = None, None

    def tmo():
        return max(RPC_FLOOR_S, min(RPC_TIMEOUT_S, deadline - clock()))

    try:
        url = endpoint(transport, cand["shop"], timeout=min(CARD_TIMEOUT_S, tmo()))
        if not url:
            return None, dict(info, result="error", why="no endpoint")
        t = rpc(transport, url, f"I want to buy {name} ({cand['sku']})", timeout=tmo())
        for _ in range(len(cand["offers"]) + 1):
            if state(t) != INPUT_REQUIRED:
                break
            if clock() >= deadline:
                info["result"] = "timeout"
                break
            ask = ask_of(t)
            if ask is not None:
                info["final_ask"] = ask
            if ask is not None and ask <= cap:
                # An offer equal to the shop's ask accepts it: full timeout, never cut short.
                info["offers_sent"].append(ask)
                t = rpc(transport, url, str(ask), t.get("id"), timeout=RPC_TIMEOUT_S)
                continue
            offer = next(offers, None)
            if offer is None:
                info["result"] = "walked"
                break
            info["offers_sent"].append(offer)
            t = rpc(transport, url, str(offer), t.get("id"), timeout=tmo())
        else:
            if state(t) == INPUT_REQUIRED:
                info["result"] = "walked"
    except Timeout:
        info["result"], info["why"] = "timeout", "request timeout"
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        info["result"], info["why"] = "error", type(e).__name__

    if url and state(t) == INPUT_REQUIRED:
        cancel(transport, url, t.get("id"))
    if info["result"] is not None:
        return None, info
    if t is None:
        return None, dict(info, result="error", why="no task")
    if state(t) != COMPLETED:
        return None, dict(info, result="rejected", why=state(t) or "no state")
    rec = receipt_of(t)
    if rec is None:
        return None, dict(info, result="error", why="no receipt")
    price = receipt_price(rec)
    if price is None or price <= 0 or price > cap:
        return None, dict(info, result="error", why="receipt price outside max_pay")
    sig, rec, how = verify_receipt(rec, pubkey)
    if sig is False:
        return None, dict(info, result="error", why=f"signature {how}")
    return rec, dict(info, result="accepted", sig=sig)


def execute(
    plan,
    ctx,
    order,
    traj,
    transport=http_json,
    deadline=None,
    clock=time.time,
    agent="buyer",
):
    """Run the plan's candidates in order until one yields a receipt. Returns the hook's output line (dict)."""
    budget = ctx.get("budget")
    deadline = clock() + DEADLINE_S if deadline is None else deadline
    shops = {s.get("url"): s for s in ctx.get("shops") or [] if isinstance(s, dict)}
    pubkeys = ctx.get("pubkeys") or {}
    cands = plan.get("candidates") or []
    tried, out, stop = 0, None, None
    for cand in cands:
        if clock() >= deadline:
            stop = "deadline"
            break
        names = {
            o.get("sku"): o.get("name")
            for o in (shops.get(cand["shop"]) or {}).get("offers") or []
            if isinstance(o, dict)
        }
        name = names.get(cand["sku"]) or cand["sku"]
        t0 = clock()
        rec, info = haggle(
            cand, name, budget, deadline, transport, pubkeys.get(cand["shop"]), clock
        )
        tried += 1
        traj.append(
            agent,
            "buyer_exec",
            event="attempt",
            order=order.get("order"),
            shop=c.hid(cand["shop"]),
            sku=cand["sku"],
            max_pay=cand["max_pay"],
            latency_s=round(clock() - t0, 3),
            **info,
        )
        if rec is not None:
            price = receipt_price(rec)
            out = {
                "shop": cand["shop"],
                "receipt": rec,
                "stars": 4,
                "review": f"{name} at {price}",
            }
            break
    if out is None:
        first = cands[0]["shop"] if cands else next(iter(shops), "")
        out = {"shop": first or "", "receipt": None, "stars": 3, "review": "no deal"}
    if plan.get("accept"):
        out["accept"] = dict(plan["accept"])
    traj.append(
        agent,
        "buyer_exec",
        event="outcome",
        order=order.get("order"),
        deal=out["receipt"] is not None,
        shop=c.hid(out["shop"]) if out["receipt"] is not None else None,
        price=receipt_price(out["receipt"]) if out["receipt"] else None,
        candidates=len(cands),
        tried=tried,
        stop=stop,
    )
    return out


def main(argv=None):
    """Hook entry: build the situation, one buyer.plan decision, execute it, print the output line last."""
    from kernel import memory
    from kernel.agent import Agent
    from kernel.llm import Terra
    from kernel.replay import CLI, HARNESS, MARKET_BRIEF, buyer_situation
    from kernel.trajectory import Trajectory

    t0 = time.time()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item")
    ap.add_argument("--budget", type=int)
    ap.add_argument("--order", required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--cli", default=CLI)
    ap.add_argument("--deadline-s", type=float, default=DEADLINE_S)
    a = ap.parse_args(argv)
    root = c.data_root(a.root)
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets=secrets)
    order = c.load_json(a.order, {}) or {}
    if a.budget and not order.get("budget"):
        order["budget"] = a.budget
    shops = c.load_json(a.shops, []) or []
    catalog = (
        c.load_json(os.path.join(os.path.dirname(a.order), "catalog.json"), {}) or {}
    )
    if isinstance(catalog, list):
        catalog = {x.get("sku"): x for x in catalog if isinstance(x, dict)}
    kind, situation, ctx = buyer_situation(order, shops, catalog, login)
    ctx["pubkeys"] = {
        s["url"]: s["pubkey"]
        for s in shops
        if isinstance(s, dict) and s.get("url") and s.get("pubkey")
    }
    agent = Agent(
        "buyer",
        os.path.join(HARNESS, "buyer"),
        Terra(traj, cli=a.cli, secrets=secrets),
        traj,
        MARKET_BRIEF,
        mem_sock=memory.sock_path(root),
    )
    plan, _ = agent.decide(kind, situation, ctx)
    out = execute(plan, ctx, order, traj, deadline=t0 + a.deadline_s)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
