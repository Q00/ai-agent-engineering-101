"""Round 3: where the shop's money goes besides stock it bought, one "[loss]" line each in the kit's log.

The kit logs a "[broken]" line only for what my buyer bought. Nothing tells the shop that a unit it sold broke, that a food
unit lost a grade or expired, or what storage costs, so the restock hook (every 60 s; its stderr reaches
logs/market-kit.log as "[buyer] ...") runs check() and prints:

    [loss] break_refund <name> <amount> ...          a unit I sold broke within the warranty I gave: the price comes back off
    [loss] break_no_warranty <name> 0 ...            it broke outside any warranty: no refund, but the buyer's surplus is 0
    [loss] break_unknown_warranty <name> <price> ... it broke and neither the ledger nor my records say the warranty
    [loss] expired <name> <cost> ...                 a food unit passed its date in stock: its cost is gone
    [loss] grade_drop <name> 0 A->B ...              a food unit fell a grade while waiting (no money yet, a cheaper sale later)
    [loss] storage <n> goods units <amount>          storage for the last hour(s), at inventory.json's storage_per_day
    [loss] checkout_refused_warranty <sku> 0 ...     my buyer's kit would not pay: the shop's warranty was too short

Sources, all of them the market's own: inventory.json, the Round 3 ledger rows market_model keeps (kinds settle and
broken), and the kit's own "[acp] settled ... ledger #N" lines, which say which settles were mine. A refund kind in the
ledger is not documented, so none is assumed. Each loss also goes to memory/agent/losses.jsonl and the daily note, and the
running totals to .market/loss_state.json, which the scorecard reads. Profit itself is the balance (refunds and storage
come off it at the market); this file says where it went. Nothing here can stop a restock.
"""
import json
import os
import re
import sys
import time

import agent_memory
import market_model

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = "loss_state.json"
SETTLED = re.compile(r"\[acp\] settled (.+?) at (\d+), ledger #(\d+)(?:, unit (\S+) grade (\S+))?")
STORAGE_RATE = 0.01          # agent.md: goods in stock cost 1% of their cost per day


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def scan_kit_log(root, state):
    """Adds my settles from the kit's log, read on from where the last run stopped (from the start after a rotation)."""
    path = os.path.join(root, "logs", "market-kit.log")
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    pos = state.get("log_pos", 0)
    if size < pos:
        pos = 0
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read()
    end = data.rfind(b"\n") + 1                       # a line still being written waits for the next run
    for line in data[:end].decode("utf-8", errors="replace").splitlines():
        m = SETTLED.search(line)
        if m:
            state["mine"][m.group(3)] = {"name": m.group(1), "amount": int(m.group(2)), "unit": m.group(4)}
    state["log_pos"] = pos + end


def warranty_of(unit, sold_memory):
    for n in reversed(sold_memory):
        if unit and n.get("unit") == unit and n.get("result") == "deal":
            return n.get("warranty") or 0
    return None


def sold_records():
    try:
        import shop_memory
        return shop_memory.load().get("negotiations", [])
    except Exception:   # noqa: BLE001 - without them a warranty is "unknown", not a crash
        return []


def check(md, root=HERE, now=None, sold=None, emit=None, rows=None):
    """The losses since the last run, each printed and remembered. Returns them. rows: the ledger rows the caller has
    already read (restock.rows_of), so a run inside a supply window does not read the 143 MB file once more."""
    now = now or time.time()
    emit = emit or (lambda line: print(line, file=sys.stderr, flush=True))
    try:
        inv = load(os.path.join(md, "inventory.json"), {})
        if inv.get("round", 0) < market_model.ECON_ROUND:
            return []
        path = os.path.join(md, STATE)
        state = {"units": {}, "mine": {}, "breaks_seen": [], "expired_seen": [], "storage_at": now, "totals": {}, **load(path, {})}
        out = []
        cur = {u["unit"]: u for u in inv.get("units") or [] if u.get("unit")}
        scan_kit_log(root, state)
        sold_units = {m.get("unit") for m in state["mine"].values() if m.get("unit")}

        def loss(kind, name, amount, detail=""):
            out.append({"ts": int(now), "kind": kind, "name": name, "amount": round(amount), "detail": detail})

        for uid, u in cur.items():
            gone = u.get("grade") == "expired" or (u.get("expires_at") and u["expires_at"] <= now)
            if gone and uid not in state["expired_seen"]:
                state["expired_seen"].append(uid)
                loss("expired", u.get("name") or u.get("sku"), u.get("cost") or 0, f"sku {u.get('sku')} unit {uid}")
            was = state["units"].get(uid)
            if was and u.get("kind") == "food" and not gone and was.get("grade") in ("A", "B", "C") \
                    and u.get("grade") in ("A", "B", "C") and u["grade"] > was["grade"]:
                loss("grade_drop", u.get("name") or u.get("sku"), 0, f"{was['grade']}->{u['grade']} sku {u.get('sku')} unit {uid}")
        for uid, was in state["units"].items():       # gone between two runs: expired at the market, or sold
            if uid not in cur and uid not in sold_units and was.get("expires_at") and was["expires_at"] <= now + 120 \
                    and uid not in state["expired_seen"]:
                state["expired_seen"].append(uid)
                loss("expired", was.get("name") or was.get("sku"), was.get("cost") or 0, f"sku {was.get('sku')} unit {uid}")

        hours = (now - state["storage_at"]) / 3600
        if hours >= 1:
            rate = inv.get("storage_per_day")
            rate = rate if isinstance(rate, (int, float)) and 0 < rate < 1 else STORAGE_RATE
            goods = [u for u in cur.values() if u.get("kind") == "goods"]
            amount = sum((u.get("cost") or 0) * rate * hours / 24 for u in goods)
            if amount >= 1:
                loss("storage", f"{len(goods)} goods units", amount, f"{hours:.1f} h at {rate:.2%}/day")
            state["storage_at"] = now

        pay = os.path.join(md, "payments.jsonl")        # my buyer's payments the kit refused for a short warranty
        if os.path.exists(pay):
            pos = state.get("pay_pos", 0)
            pos = 0 if os.path.getsize(pay) < pos else pos
            with open(pay, "rb") as f:
                f.seek(pos)
                data = f.read()
            end = data.rfind(b"\n") + 1
            for line in data[:end].decode("utf-8", errors="replace").splitlines():
                try:
                    p = json.loads(line)
                except ValueError:
                    continue
                if isinstance(p, dict) and not p.get("paid") and "warranty" in str(p.get("reason")):
                    loss("checkout_refused_warranty", p.get("item"), 0, f"shop {p.get('shop')} price {p.get('price')}: {p.get('reason')}")
            state["pay_pos"] = pos + end
        rows = market_model.load_rows(md) if rows is None else rows
        records = sold_records() if sold is None else sold
        for b, s in market_model.link_broken(rows):
            mine = state["mine"].get(str(s["seq"]))
            if not mine or b["seq"] in state["breaks_seen"]:
                continue
            state["breaks_seen"].append(b["seq"])
            after = (b["ts"] - s["ts"]) / 3600
            w = s.get("warranty")
            w = w if isinstance(w, (int, float)) else warranty_of(mine.get("unit"), records)
            name, price = mine.get("name") or s.get("sku"), s.get("amount") or mine["amount"]
            detail = f"sku {s.get('sku')} buyer {s.get('buyer')} ledger #{s['seq']} broke after {after:.1f} h, warranty {w} h"
            if w is None:
                loss("break_unknown_warranty", name, price, detail)
            elif w >= after:
                loss("break_refund", name, price, detail)
            else:
                loss("break_no_warranty", name, 0, detail)

        state["units"] = {uid: {k: u.get(k) for k in ("sku", "name", "kind", "grade", "cost", "expires_at")} for uid, u in cur.items()}
        state["expired_seen"] = state["expired_seen"][-2000:]
        state["breaks_seen"] = state["breaks_seen"][-5000:]
        for x in out:
            t = state["totals"].setdefault(x["kind"], {"n": 0, "amount": 0})
            t["n"] += 1
            t["amount"] += x["amount"]
            emit(f"[loss] {x['kind']} {x['name']} {x['amount']} {x['detail']}".rstrip())
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
        if out:
            os.makedirs(agent_memory.root(), exist_ok=True)
            with open(os.path.join(agent_memory.root(), "losses.jsonl"), "a", encoding="utf-8") as f:
                for x in out:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
            money = [x for x in out if x["amount"]]
            if money:
                agent_memory.daily(md, "loss", "; ".join(f"{x['kind']} {x['name']} {x['amount']}" for x in money)[:400])
        return out
    except Exception as e:   # noqa: BLE001
        print(f"[loss] check failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return []
