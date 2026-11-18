"""Challenge 2 buyer — a shopping list, one total budget, a directory of ~20 shops.

    python shop_buyer.py                                  # directory.json + shopping_list.json
    python shop_buyer.py --directory directory.json --list shopping_list.json

1. Read every Agent Card in the directory (the DF / yellow pages step).
2. The harness shortlists up to two shops per list item from the cards.
3. Ask each shortlisted shop for a quote (one A2A task per shop and item).
4. The harness picks one shop per item and a price cap per item (caps must fit the budget).
5. Negotiate all items at once, one thread per item. Each harness call sees a short
   status board (one line per item) instead of every conversation. If the chosen shop
   fails, the item moves to its backup quote. Unused quote tasks are canceled.

Budget guard in code: an offer may never exceed  budget - spent - caps of the other open items.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from buyer import A2AClient, last_number, receipt_price, state, status_text

MAX_TURNS = 8
CLAUDE = shutil.which("claude") or "claude"
HARNESS_CMD = [CLAUDE, "-p", "--model", "haiku", "--tools", "",
               "--setting-sources", "", "--no-session-persistence"]
if os.environ.get("HARNESS_CMD"):
    HARNESS_CMD = json.loads(os.environ["HARNESS_CMD"])
WORKDIR = tempfile.mkdtemp(prefix="shop-buyer-")

LOCK = threading.Lock()
os.makedirs(os.path.join("logs", "challenge-2"), exist_ok=True)
STAMP = time.strftime("%Y%m%d-%H%M%S")
LOG_PATH = os.path.join("logs", "challenge-2", f"buyer-{STAMP}.txt")


def log(*parts):
    line = time.strftime("%H:%M:%S ") + " ".join(str(p) for p in parts)
    with LOCK:
        print(line, flush=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def harness_json(prompt, label):
    for _ in range(2):
        try:
            r = subprocess.run(HARNESS_CMD, input=prompt, capture_output=True, text=True,
                               encoding="utf-8", cwd=WORKDIR, timeout=120)
            raw = r.stdout.strip()
            log(f"harness({label}, prompt {len(prompt)} chars): {raw!r}")
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                return json.loads(m.group())
        except Exception as e:
            log(f"harness({label}) error: {e}")
    return None


SHORTLIST_PROMPT = """You are a shopping agent. Shopping list:
{items}

Shops in the directory (from their Agent Cards; what a card claims about prices is the shop's own claim):
{cards}

For each list item, pick up to two shops whose cards say they sell that kind of item.
Reply with ONE JSON object only, no other text:
{{"shortlist": [[<shop numbers for item 0>], [<shop numbers for item 1>], ...]}}
"""

PLAN_PROMPT = """You are a shopping agent with a total budget of {budget} for this whole list.
Quotes you received (opening asks; shops usually come down 10-25% when negotiating):
{quotes}

For each item choose one quote (the item must fit what the list asked for) and a price cap
for that item. The caps together must not exceed {budget}.
Reply with ONE JSON object only, no other text:
{{"plan": [{{"item": <item number>, "quote": "<quote label like 0b>", "cap": <integer>}}, ...]}}
"""

TURN_PROMPT = """You are a shopping agent negotiating several purchases at once. Total budget {budget}, spent so far {spent}.
Status board:
{board}

Now act on item [{i}] "{want}" at {shop}.
The shop's last message: "{last_msg}"
The shop's current ask: {ask}. Your last offer here: {last}.
The most you may pay for this item right now (budget minus spent minus caps kept for other open items): {limit}.

Make an offer below the ask and raise gradually, accept the ask if it is within your limit and the shop
has stopped coming down, or walk away if the shop will not come down to your limit.
Reply with ONE JSON object only, no other text:
{{"action": "offer" | "accept" | "walk", "price": <your offer, integer, only for offer>}}
"""


class Buyer:
    def __init__(self, urls, wants, budget):
        self.wants, self.budget = wants, budget
        self.shops = []  # (client, card line)
        for url in urls:
            try:
                c = A2AClient(url)
            except Exception as e:
                log(f"card failed {url}: {e}")
                continue
            card = c.card
            skills = "; ".join(s.get("description", "") for s in card.get("skills", []))
            self.shops.append((c, f"{card.get('name')}: {card.get('description', '')} Skill: {skills}"))
        log(f"read {len(self.shops)} cards ({sum(len(l) for _, l in self.shops)} chars)")
        self.spent = 0
        self.status = {i: {"shop": "-", "ask": None, "last": None, "state": "planning", "price": None}
                       for i in range(len(wants))}
        self.caps = {}
        self.receipts = {}

    # ---------- steps 2-4 ----------
    def shortlist(self):
        cards = "\n".join(f"[{k}] {line}" for k, (_, line) in enumerate(self.shops))
        items = "\n".join(f"{i}. {w}" for i, w in enumerate(self.wants))
        d = harness_json(SHORTLIST_PROMPT.format(items=items, cards=cards), "shortlist") or {}
        out = []
        for i in range(len(self.wants)):
            try:
                ks = [int(k) for k in d["shortlist"][i] if 0 <= int(k) < len(self.shops)][:2]
            except (KeyError, IndexError, TypeError, ValueError):
                ks = []
            out.append(ks or [0, 1])
            log(f"shortlist item {i}: {[self.shops[k][0].card.get('name') for k in out[-1]]}")
        return out

    def get_quotes(self, shortlist):
        jobs = [(i, k, chr(ord('a') + n)) for i, ks in enumerate(shortlist) for n, k in enumerate(ks)]

        def quote(job):
            i, k, tag = job
            client = self.shops[k][0]
            try:
                task = client.send(self.wants[i])
            except Exception as e:
                log(f"quote {i}{tag} failed: {e}")
                return None
            text = status_text(task)
            log(f"quote {i}{tag} {client.card.get('name')}: {state(task)} | {text}")
            if state(task) != "TASK_STATE_INPUT_REQUIRED":
                return None
            return {"label": f"{i}{tag}", "item": i, "shop": k, "task": task, "ask": last_number(text), "text": text}

        with ThreadPoolExecutor(max_workers=8) as ex:
            quotes = [q for q in ex.map(quote, jobs) if q]
        return quotes

    def plan(self, quotes):
        lines = "\n".join(f"{q['label']} item {q['item']} ({self.wants[q['item']]}) at {self.shops[q['shop']][0].card.get('name')}: "
                          f"{q['text'][:300]}" for q in quotes)
        d = harness_json(PLAN_PROMPT.format(budget=self.budget, quotes=lines), "plan") or {}
        by_label = {q["label"]: q for q in quotes}
        order = {}
        for p in d.get("plan", []):
            q = by_label.get(str(p.get("quote")))
            if q is None or q["item"] in order:
                continue
            try:
                self.caps[q["item"]] = int(p.get("cap"))
            except (TypeError, ValueError):
                self.caps[q["item"]] = q["ask"] or 0
            order[q["item"]] = [q] + [o for o in quotes if o["item"] == q["item"] and o is not q]
        for i in range(len(self.wants)):  # items the plan forgot: cheapest quote first
            if i not in order:
                mine = sorted([q for q in quotes if q["item"] == i], key=lambda q: q["ask"] or 10**9)
                order[i] = mine
                self.caps[i] = mine[0]["ask"] if mine else 0
        total = sum(self.caps.values())
        if total > self.budget:  # guard: caps must fit the budget
            f = self.budget / total
            self.caps = {i: int(c * f) for i, c in self.caps.items()}
            log(f"GUARD caps summed to {total} > budget {self.budget}; scaled to {self.caps}")
        log(f"plan: caps {self.caps}, total {sum(self.caps.values())} of {self.budget}")
        return order

    # ---------- step 5 ----------
    def limit(self, i):
        with LOCK:
            others = sum(self.caps[j] for j, s in self.status.items() if j != i and s["state"] in ("planning", "negotiating"))
            return self.budget - self.spent - others

    def board(self):
        lines = []
        for i, s in self.status.items():
            if s["state"] == "done":
                lines.append(f"[{i}] {self.wants[i][:40]}: DONE at {s['price']} ({s['shop']})")
            elif s["state"] == "failed":
                lines.append(f"[{i}] {self.wants[i][:40]}: FAILED")
            else:
                lines.append(f"[{i}] {self.wants[i][:40]}: {s['state']} at {s['shop']}, ask {s['ask']}, "
                             f"your last {s['last']}, cap {self.caps.get(i)}")
        return "\n".join(lines)

    def negotiate(self, q):
        i, client = q["item"], self.shops[q["shop"]][0]
        shop, task = client.card.get("name"), q["task"]
        tid = task.get("id")
        st = self.status[i]
        st.update(shop=shop, state="negotiating", last=None)
        for _ in range(MAX_TURNS):
            text = status_text(task)
            if state(task) == "TASK_STATE_COMPLETED":
                price = receipt_price(task)
                with LOCK:
                    self.spent += price
                    st.update(state="done", price=price)
                    self.receipts[i] = {"shop": shop, "receipt": [p.get("data") for a in task.get("artifacts", []) for p in a.get("parts", []) if p.get("data")]}
                log(f"[{i}] DEAL at {price} with {shop}; spent {self.spent} of {self.budget}")
                return True
            if state(task) != "TASK_STATE_INPUT_REQUIRED":
                log(f"[{i}] {shop} ended: {state(task)} | {text}")
                return False
            ask, lim = last_number(text), self.limit(i)
            st["ask"] = ask
            prompt = TURN_PROMPT.format(budget=self.budget, spent=self.spent, board=self.board(), i=i,
                                        want=self.wants[i], shop=shop, last_msg=text[-300:], ask=ask,
                                        last=st["last"] if st["last"] is not None else "none yet", limit=lim)
            d = harness_json(prompt, f"turn {i}") or {"action": "walk"}
            if d.get("action") == "walk":
                log(f"[{i}] WALK from {shop}")
                client.cancel(tid)
                return False
            if d.get("action") == "accept" and ask is not None:
                offer = ask
            else:
                try:
                    offer = int(round(float(d.get("price"))))
                except (TypeError, ValueError):
                    offer = st["last"] or 0
            if offer > lim:
                log(f"[{i}] GUARD offer {offer} > limit {lim} -> {lim}")
                offer = lim
            st["last"] = offer
            log(f"[{i}] -> {shop}: {d.get('action')} {offer} (ask {ask}, limit {lim})")
            task = client.send(str(offer), tid)
        log(f"[{i}] turn limit at {shop}")
        client.cancel(tid)
        return False

    def run_item(self, i, quotes):
        for n, q in enumerate(quotes):
            if n > 0:
                log(f"[{i}] trying backup quote {q['label']}")
            if self.negotiate(q):
                for other in quotes[n + 1:]:  # never buy the same item twice
                    self.shops[other["shop"]][0].cancel(other["task"].get("id"))
                return
        with LOCK:
            self.status[i]["state"] = "failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", default="directory.json")
    ap.add_argument("--list", default="shopping_list.json")
    args = ap.parse_args()
    urls = json.load(open(args.directory, encoding="utf-8"))
    spec = json.load(open(args.list, encoding="utf-8"))
    wants, budget = spec["items"], int(spec["budget"])
    start = time.time()
    log(f"# challenge 2 run {STAMP}: {len(wants)} items, budget {budget}, {len(urls)} shops in directory")

    b = Buyer(urls, wants, budget)
    quotes = b.get_quotes(b.shortlist())
    order = b.plan(quotes)
    with ThreadPoolExecutor(max_workers=len(wants)) as ex:
        list(ex.map(lambda i: b.run_item(i, order.get(i, [])), range(len(wants))))

    bought = {i: b.status[i]["price"] for i in range(len(wants)) if b.status[i]["state"] == "done"}
    total = sum(bought.values())
    checks = {"every item bought": len(bought) == len(wants),
              "total within budget": total <= budget,
              "no item twice": all(len(r["receipt"]) == 1 for r in b.receipts.values())}
    log(f"\n## result after {time.time() - start:.0f}s")
    log(b.board())
    log(f"total {total} of {budget}")
    for k, v in checks.items():
        log(f"{'PASS' if v else 'FAIL'}  {k}")
    log("PASS" if all(checks.values()) else "FAIL")
    with open(os.path.join("logs", "challenge-2", f"summary-{STAMP}.json"), "w", encoding="utf-8") as f:
        json.dump({"budget": budget, "total": total, "checks": checks, "status": b.status,
                   "caps": b.caps, "receipts": b.receipts}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()