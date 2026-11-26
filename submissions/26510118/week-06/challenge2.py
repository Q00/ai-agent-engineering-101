"""Week 06, challenge 2 -- one buyer, twenty shops, one shopping list, one budget.

Twenty copies of seller.py are started from shops.json, each with a slice of
the catalogue and its own price level, so that the Agent Cards are worth
reading. The buyer is given their addresses and nothing else: it fetches the
cards itself, which is the Directory Facilitator from the lecture with the
directory reduced to a list of URLs.

    python challenge2.py              # tight budget, the real run
    python challenge2.py --tightness 0.70   # too little money, on purpose

The hard part is not the protocol, it is that the context grows. Four open
negotiations across twenty shops, several rounds each, and a harness with no
memory between calls: the whole situation has to be restated every time it is
asked anything. Two things keep that bounded.

    The board. Each negotiation is one fixed line -- shop, their ask, my last
    offer, which round, state -- not a transcript. A negotiation in round 7
    costs the same characters as one in round 1, and `prompt_chars` in the
    summary records that the prompt does not grow with the rounds.

    The shortlist. Twenty cards are narrowed to three per item by word overlap
    in code before the harness sees anything, the same split challenge 1 uses
    on the catalogue.

Guarantees stay in code, as everywhere else in this submission: the total
spend cannot exceed the budget, an item cannot be bought twice, and no offer
above an item's cap is ever sent. The harness decides which shop, how to split
the budget, and what to offer.
"""
import argparse
import asyncio
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

import catalog
import harness
from buyer import last_number, message_text, settled, state

HERE = Path(__file__).parent
LOGS = HERE / "logs" / "challenge-2"          # --tag puts a run in a subfolder

# (label, what the buyer says). Descriptions, not names, so challenge 1's
# catalogue search runs inside every shop as well.
SHOPPING_LIST = [
    ("lamp", "a reading lamp for the study"),
    ("bookshelf", "something wooden to hold about 60 books"),
    ("stool", "a stool for the kitchen"),
    ("mirror", "a mirror for the hallway"),
]

SHORTLIST = 3            # shops asked for a quote per item
ROUNDS = 4               # haggling rounds after the quote
HARNESS_AT_ONCE = 3      # concurrent harness calls; more makes the service slower

LOG_LINES = []


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    LOG_LINES.append(line)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------- the A2A side

class Shop:
    """One remote shop. Everything the lab's a2a CLI did, async."""

    def __init__(self, base: str, http: httpx.AsyncClient):
        self.base, self.http, self.card, self.url = base, http, None, base

    async def fetch_card(self):
        r = await self.http.get(self.base + "/.well-known/agent-card.json")
        self.card = r.json()
        iface = (self.card.get("supportedInterfaces") or [{}])[0]
        self.url = iface.get("url") or self.base
        return self.card

    @property
    def name(self):
        return (self.card or {}).get("name", self.base)

    @property
    def blurb(self):
        c = self.card or {}
        skills = " ".join(s.get("description", "") for s in c.get("skills") or [])
        return f"{c.get('name', '')} {c.get('description', '')} {skills}"

    async def rpc(self, method, params):
        body = {"jsonrpc": "2.0", "id": "c2", "method": method, "params": params}
        r = await self.http.post(self.url, json=body,
                                 headers={"A2A-Version": "1.0"})      # never optional
        d = r.json()
        if "error" in d:
            log(f"    <- {self.name}: error {d['error'].get('code')} "
                f"{str(d['error'].get('message'))[:90]}")
            return None
        return d["result"].get("task", d["result"])

    async def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": f"m{time.time_ns()}", "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        return await self.rpc("SendMessage", {"message": msg})

    async def cancel(self, task_id):
        await self.rpc("CancelTask", {"name": f"tasks/{task_id}", "id": task_id})


# ------------------------------------------------------- narrowing, in code

PICK_SHOPS_PROMPT = """A shopper is looking for: "{request}"

The shops in the directory:
{cards}

Reply with ONE line and nothing else: the numbers of the {k} shops most likely
to have it, separated by spaces."""


def _stem_hits(want: set, have: set) -> int:
    """Overlap, but "books" counts against "bookshelf".

    Plain equality found no shop at all for "something wooden to hold about 60
    books": the cards say bookshelf, the shopper said books. Four letters of
    agreement is enough to pair them and short enough to stay cheap.
    """
    n = 0
    for w in want:
        if any(w == h or (len(w) >= 4 and len(h) >= 4 and
                          (h.startswith(w[:4]) or w.startswith(h[:4]))) for h in have):
            n += 1
    return n


async def shortlist(shops, request, sem, sizes, k=SHORTLIST):
    """The k shops whose cards best match the request.

    Word overlap first, because twenty cards do not need a model to sort. The
    cheap-shop bonus comes from the card too: every card says whether it is a
    discount shop, and on a four-way tie for "mirror" the alphabet was picking
    the dearest of them. Only when the words find nothing does the harness
    read the cards instead.
    """
    want = set(catalog.tokens(request))
    scored = []
    for s in shops:
        hits = _stem_hits(want, set(catalog.tokens(s.blurb)))
        if hits:
            cheap = 0.5 if "discount" in s.blurb.lower() else 0.0
            scored.append((hits + cheap, s))
    if scored:
        scored.sort(key=lambda p: (-p[0], p[1].name))
        return [s for _, s in scored[:k]]

    cards = "\n".join(f"{i}. {s.name} -- {s.card['description'][:90]}"
                      for i, s in enumerate(shops, 1))
    out = await ask_harness(PICK_SHOPS_PROMPT.format(request=request, cards=cards, k=k),
                            sem, sizes)
    idx = [int(n) for n in re.findall(r"\d+", out or "") if 1 <= int(n) <= len(shops)]
    log(f"    (no word overlap; harness chose shops {idx} from the cards)")
    return [shops[i - 1] for i in idx[:k]]


# ----------------------------------------------------------- the board

def board(stateboard, budget, spent):
    """Every negotiation in one fixed line. This is the answer to "the
    conversation grows with every shop": it does not, because what is carried
    forward is a position, not a transcript."""
    rows = [f"BUDGET {budget}, spent {spent}, left {budget - spent}", "",
            f"  {'item':<11}{'shop':<18}{'ask':>6}{'my last':>9}{'round':>7}  state"]
    for label, st in stateboard.items():
        rows.append(f"  {label:<11}{st['shop']:<18}{str(st['ask'] or '-'):>6}"
                    f"{str(st['mine'] or '-'):>9}{st['round']:>7}  {st['state']}")
    return "\n".join(rows)


OFFER_PROMPT = """You are one buyer doing all the shopping below with one shared budget.

{board}

You are now dealing with {shop} for the {label} ({request}).
Their ask is {ask}. You may not go over {cap} for this item.

Reply with ONE line and nothing else:
  ACCEPT          pay their ask
  a whole number  your counteroffer

Everything on the list has to be bought, so there is no option to give up."""

SPLIT_PROMPT = """You are shopping for all of these with ONE budget of {budget}.

The cheapest list price found for each, before any haggling:
{quotes}
Those add up to {total}, which is more than the budget, so every one of them
has to come down. Shops here typically give 10 to 20 percent off their list
price when pushed.

Split the budget into the most you are willing to pay for each item.
Reply with one line per item, "<name>: <number>", and nothing else."""


def parse_one(out, fallback):
    s = (out or "").strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    up = s.upper()
    if "ACCEPT" in up:
        return "ACCEPT"
    if "WALK" in up or "REJECT" in up:
        return "WALK"
    m = re.fullmatch(r"[^\d]*?(\d+)[^\d]*", s)
    return int(m.group(1)) if m else fallback


def cap_for(label, live, sb, caps, budget, spent):
    """What this item may actually spend right now.

    The split from the harness is a plan, not a limit -- the limit is the one
    budget. A first run gave the lamp a cap of 57, the shop came down to 58,
    the lamp was abandoned and the run ended with 57 unspent. So each item may
    take whatever the still-open others will not need: their own cap, or their
    current ask if that is lower. The total still cannot be exceeded, which is
    the only promise that matters.
    """
    others = [l for l in live if l != label and sb[l]["state"] == "OPEN"]
    reserved = sum(min(caps[l], sb[l]["ask"] or caps[l]) for l in others)
    return max(0, budget - spent - reserved)


async def ask_harness(prompt, sem, sizes):
    sizes.append(len(prompt))
    async with sem:
        return await asyncio.to_thread(harness.ask, prompt)


# ------------------------------------------------------------------- run

async def run(tightness: float, tag: str = "") -> int:
    global LOGS
    if tag:
        LOGS = LOGS / tag
    LOGS.mkdir(parents=True, exist_ok=True)
    specs = json.loads((HERE / "shops.json").read_text(encoding="utf-8"))
    procs, bases = [], []
    shop_log = (LOGS / "shops.log").open("w", encoding="utf-8")

    for spec in specs:
        port = free_port()
        procs.append(subprocess.Popen(
            [sys.executable, "seller.py", "--port", str(port), "--name", spec["name"],
             "--sells", ",".join(spec["sells"]), "--mult", str(spec["mult"])],
            cwd=HERE, stdout=shop_log, stderr=subprocess.STDOUT))
        bases.append(f"http://127.0.0.1:{port}")
    (LOGS / "directory.json").write_text(json.dumps(bases, indent=1) + "\n", encoding="utf-8")

    sizes, summary = [], {}
    try:
        async with httpx.AsyncClient(timeout=120) as http:
            shops = [Shop(b, http) for b in bases]
            # --- discovery: the buyer reads the directory itself ------------
            for attempt in range(60):
                try:
                    await asyncio.gather(*(s.fetch_card() for s in shops))
                    break
                except Exception:
                    await asyncio.sleep(1)
            sem = asyncio.Semaphore(HARNESS_AT_ONCE)
            log(f"## directory: {len(shops)} shops")
            for s in shops:
                log(f"   {s.name:<18} {s.card['description'][:74]}")

            # --- quotes: three shops per item, in parallel ------------------
            log("\n## quotes")
            quotes = {}
            for label, request in SHOPPING_LIST:
                picks = await shortlist(shops, request, sem, sizes)
                log(f"  {label}: shortlist {[s.name for s in picks]}")
                tasks = await asyncio.gather(*(s.send(f"I want to buy {request}")
                                               for s in picks))
                got = []
                for s, t in zip(picks, tasks):
                    ask = last_number(t) if state(t) == "TASK_STATE_INPUT_REQUIRED" else None
                    if ask:
                        got.append({"shop": s, "task": t["id"], "ask": ask})
                        log(f"    {s.name:<18} {message_text(t)[:62]}")
                got.sort(key=lambda q: q["ask"])
                quotes[label] = got

            # --- the budget, set from the quotes so it has to bite ----------
            best = {k: v[0]["ask"] for k, v in quotes.items() if v}
            budget = round(sum(best.values()) * tightness)
            log(f"\n## budget {budget} = {sum(best.values())} (cheapest asks) x {tightness}")

            # --- the split: a judgement, so the harness makes it ------------
            qlines = "\n".join(f"  {k:<11}{quotes[k][0]['shop'].name:<18}{v:>6}"
                               for k, v in best.items())
            out = await ask_harness(SPLIT_PROMPT.format(budget=budget, quotes=qlines,
                                                        total=sum(best.values())), sem, sizes)
            caps = {}
            for label in best:
                m = re.search(rf"{re.escape(label)}\D{{0,4}}(\d+)", out or "", re.I)
                if m:
                    caps[label] = int(m.group(1))
            if set(caps) != set(best) or sum(caps.values()) > budget:
                share = {k: budget * v / sum(best.values()) for k, v in best.items()}
                caps = {k: int(v) for k, v in share.items()}
                log(f"   harness split unusable ({out!r}); splitting in proportion instead")
            log(f"   caps {caps}")

            # --- drop the quotes we will not use ---------------------------
            keep = {label: quotes[label][0] for label in caps if quotes.get(label)}
            # The runners-up are released but remembered. A shop that ends a
            # negotiation is not the end of the item: the directory had three
            # candidates for it, and a stress run with too little money left
            # 360 unspent because a lamp asking 48 had walked away.
            alts = {label: [q["shop"] for q in qs[1:]] for label, qs in quotes.items()}
            for label, qs in quotes.items():
                for q in qs:
                    if q is not keep.get(label):
                        await q["shop"].cancel(q["task"])

            # --- negotiate, all items in lockstep --------------------------
            sb = {label: {"shop": keep[label]["shop"].name, "ask": keep[label]["ask"],
                          "mine": None, "round": 0, "state": "OPEN"} for label in keep}
            spent, bought, receipts = 0, {}, []

            for rnd in range(1, ROUNDS + 1):
                for label, st in sb.items():
                    if label in bought or st["state"] == "OPEN" or not alts.get(label):
                        continue
                    nxt = alts[label].pop(0)
                    t = await nxt.send(f"I want to buy {dict(SHOPPING_LIST)[label]}")
                    if state(t) == "TASK_STATE_INPUT_REQUIRED":
                        keep[label] = {"shop": nxt, "task": t["id"], "ask": last_number(t)}
                        st.update(shop=nxt.name, ask=last_number(t), mine=None, state="OPEN")
                        log(f"   {label}: {nxt.name} instead, asking {st['ask']}")

                live = [l for l in sb if sb[l]["state"] == "OPEN"]
                if not live:
                    break
                log(f"\n## round {rnd}: {live}")
                log(board(sb, budget, spent))

                prompts = []
                for label in live:
                    q, st = keep[label], sb[label]
                    cap = cap_for(label, live, sb, caps, budget, spent)
                    prompts.append(ask_harness(OFFER_PROMPT.format(
                        board=board(sb, budget, spent), shop=q["shop"].name, label=label,
                        request=dict(SHOPPING_LIST)[label], ask=st["ask"], cap=cap), sem, sizes))
                answers = await asyncio.gather(*prompts)

                sends = []
                for label, out in zip(live, answers):
                    q, st = keep[label], sb[label]
                    cap = cap_for(label, live, sb, caps, budget, spent)
                    d = parse_one(out, int(cap * 0.85))
                    affordable = st["ask"] is not None and st["ask"] <= cap
                    if d == "WALK":
                        # Abandoning an item on the list is not a judgement the
                        # harness gets to make: the run only passes if every
                        # item is bought, and money freed by a cheap purchase
                        # later in the run may well reach this one. A mirror
                        # was given up in round 1 and the run ended with 155
                        # unspent. Keep haggling at the cap instead.
                        log(f"   {label}: harness wanted out; holding at the cap")
                        d = cap
                    if affordable and (d == "ACCEPT" or rnd == ROUNDS):
                        price = st["ask"]                     # take it
                    else:
                        price = d if isinstance(d, int) else int(cap * 0.85)
                    price = min(price, cap)                   # the guarantee, in code
                    log(f"   {label}: harness {str(out)[:28]!r} -> offer {price} (cap {cap})")
                    st["mine"], st["round"] = price, rnd
                    sends.append(q["shop"].send(str(price), q["task"]))

                results = await asyncio.gather(*(s for s in sends if s is not None))
                it = iter(results)
                for label, s in zip(live, sends):
                    if s is None:
                        continue
                    t = next(it)
                    st = sb[label]
                    if state(t) == "TASK_STATE_COMPLETED":
                        # The budget arithmetic lives here, so the receipt has
                        # to be checked here. A shop charging 500 for an offer
                        # of 100, or returning a negative price, would
                        # otherwise go straight into `spent`.
                        price, problem = settled(t, st["mine"], label)
                        if problem:
                            log(f"   !! {label}: {problem}; not counting it as bought")
                            st["state"] = "BAD RECEIPT"
                            continue
                        spent += price
                        bought[label] = {"shop": st["shop"], "price": price}
                        receipts.append(label)
                        st.update(state=f"BOUGHT at {price}", ask=None, mine=None)
                        log(f"   {label}: bought at {price}, spent {spent}/{budget}")
                    elif state(t) == "TASK_STATE_INPUT_REQUIRED":
                        st["ask"] = last_number(t)
                    else:
                        st["state"] = state(t).replace("TASK_STATE_", "")

            # --- settle ---------------------------------------------------
            # The rounds are over; the money that is left is now certain. Walk
            # each unbought item's shop and then the rest of its shortlist,
            # buying at the first ask the leftover covers. Two things made this
            # necessary. Caps are fixed at the start of a round, so money freed
            # by a cheap purchase inside that round stays reserved until it
            # ends -- one run finished with a stool asking 71 against a cap of
            # 70 and 211 unspent. And a shop that ends its negotiation in the
            # final round leaves no later round to replace it: a dead task is a
            # dead shop, not a dead item.
            for label, st in sb.items():
                if label in bought:
                    continue
                log(f"\n## settle {label}: {budget - spent} left")
                for _ in range(2 * (1 + len(alts.get(label, [])))):
                    ask = st["ask"]
                    if st["state"] == "OPEN" and ask is not None and spent + ask <= budget:
                        t = await keep[label]["shop"].send(str(ask), keep[label]["task"])
                        if state(t) == "TASK_STATE_COMPLETED":
                            price, problem = settled(t, ask, label)
                            if problem:
                                log(f"   !! {label}: {problem}; not counting it")
                                st["state"] = "BAD RECEIPT"
                                break
                            spent += price
                            bought[label] = {"shop": st["shop"], "price": price}
                            receipts.append(label)
                            st.update(state=f"BOUGHT at {price}", ask=None, mine=None)
                            log(f"   {label}: bought at {price} from {st['shop']}, "
                                f"spent {spent}/{budget}")
                            break
                        if state(t) == "TASK_STATE_INPUT_REQUIRED":
                            # Not a refusal: the shop met its own asking price
                            # with a counteroffer because it wants more. It is
                            # still talking, so stay with it. Reading this as
                            # "shop finished" once walked away from a lamp at
                            # 82 and paid 146 at the next shop along.
                            st["ask"] = last_number(t)
                            log(f"   {label}: {st['shop']} now asks {st['ask']}")
                            continue
                        st["state"] = state(t).replace("TASK_STATE_", "") or "ERROR"
                    if not alts.get(label):
                        log(f"   {label}: no shop left to try")
                        break
                    nxt = alts[label].pop(0)
                    t = await nxt.send(f"I want to buy {dict(SHOPPING_LIST)[label]}")
                    if state(t) == "TASK_STATE_INPUT_REQUIRED":
                        keep[label] = {"shop": nxt, "task": t["id"], "ask": last_number(t)}
                        st.update(shop=nxt.name, ask=last_number(t), state="OPEN")
                        log(f"   {label}: trying {nxt.name}, asking {st['ask']}")

            log("\n## final")
            log(board(sb, budget, spent))

            summary = {
                "shops": len(shops), "shopping_list": dict(SHOPPING_LIST),
                "tightness": tightness, "budget": budget,
                "cheapest_asks": best, "caps": caps,
                "bought": bought, "spent": spent,
                "all_items_bought": set(bought) == set(dict(SHOPPING_LIST)),
                "within_budget": spent <= budget,
                # Counts receipts, not dictionary keys: keyed by item, a dict
                # cannot hold a duplicate, so the old check could only ever
                # say yes. A second purchase of the same item would show here.
                "receipts": receipts,
                "no_duplicates": len(receipts) == len(set(receipts)),
                "harness_calls": harness.CALLS["n"], "harness_failed": harness.CALLS["failed"],
                "prompt_chars": {"calls": len(sizes), "min": min(sizes) if sizes else 0,
                                 "max": max(sizes) if sizes else 0,
                                 "mean": round(sum(sizes) / len(sizes)) if sizes else 0},
            }
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
        shop_log.close()

    (LOGS / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    (LOGS / "run.log").write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")

    ok = summary["all_items_bought"] and summary["within_budget"] and summary["no_duplicates"]
    log(f"\nbought {len(summary['bought'])}/{len(SHOPPING_LIST)}  "
        f"spent {summary['spent']}/{summary['budget']}  "
        f"-> {'PASS' if ok else 'NOT ALL THREE'}")
    log(f"prompt chars: {summary['prompt_chars']}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tightness", type=float, default=0.88,
                    help="budget as a fraction of the cheapest asks added up")
    ap.add_argument("--tag", default="", help="write this run into logs/challenge-2/<tag>/")
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.tightness, a.tag)))
