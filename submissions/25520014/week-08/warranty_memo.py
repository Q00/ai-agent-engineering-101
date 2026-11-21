"""Warranty by buyer and grade, learned from the ledger (owner, 11-13).

Why: 11-10 20 h to 11-13 04 h about 1,900 of our goods receipts without a warranty were refused at payment, nearly
all by three buyers on one grade each (iyen-dori C 868, 11im B 724, YAHO1212 C 258): the order's `accept` asks for a
warranty and the shop never learned it. Meanwhile sergiobuilds sells most A goods without one (A 0 h: stars 4.61,
92% paid; 72 h: 4.64, 96%) and paid 161M in refunds against our 1,026M. Buyers do not pay more for a warranty; a few
refuse to pay without one. So the warranty is decided per buyer and grade, from what that buyer paid for.

The seller does not see the buyer's name, only its first message. A buyer's first message keeps its own wording, so
the message with the product name, SKUs and numbers taken out (`template`) stands for the buyer: in 72 h of our
receipts 125 templates, 78% of receipts from the template's main buyer. Our ledger results carry our task id, and
data/turns.jsonl has each task's first message, so each template gets the buyers that sent it.

What each buyer pays for comes from the whole market (every shop's receipts in the ledger, WINDOW_H hours): per
(buyer, grade, with/without a warranty) the receipts paid and refused. A template's rate is the mean of its buyers'
rates weighted by how often each sent it, over the buyers with MIN_BUYER_N receipts or more (`cov` is their share).

decide(template, grade):
  0     without a warranty it is paid: cov >= FREE_COV and rate >= FREE_RATE
  72    without one it is refused and with one it is not: cov >= NEED_COV, rate < NEED_RATE, and with a warranty
        either too little known or paid at NEED_W_RATE or more
  None  not known: the seller's own rules (A 72 h, B none, C on ask)

    python warranty_memo.py        # build once from data/ and print the table and the last 48 h replayed
"""
import collections
import json
import re
import threading
import time

import model
import policy

WINDOW_H = 48          # ledger receipts counted
TASK_H = 72            # our receipts that teach which buyers send a template
REFRESH_S = 600
MIN_BUYER_N = 3        # a buyer's rate counts from this many receipts of the grade and warranty kind
MIN_TPL_N = 5          # a template is known from this many of our receipts
FREE_COV, FREE_RATE = 0.8, 0.9
NEED_COV, NEED_RATE, NEED_W_RATE = 0.5, 0.5, 0.5
TURNS = policy.DATA / "turns.jsonl"

SKU = re.compile(r"\bG[0-9a-f]{8}\b")
NUM = re.compile(r"\d[\d,.]*")


def template(text, name=None):
    """The first message with the product name, SKUs and numbers taken out."""
    t = text or ""
    if name:
        t = t.replace(name, "<N>")
    return NUM.sub("#", SKU.sub("<S>", t)).strip()[:160]


class Memo:
    def __init__(self):
        self.lock = threading.Lock()
        self.table = {}                 # (template, grade) -> decision dict
        self.task_tpl = {}              # task -> (ts, template)
        self.turns_off = 0
        self.seq = None
        self.results = collections.deque()   # (ts, order, buyer, grade, warranted, task or None)
        self.settled = {}                    # order -> ts
        self.names = {}
        self.ours = set()
        self.ready = False

    # ---------- reading ----------
    def _read_turns(self, now):
        """First message of each task since TASK_H, read on from where the last call stopped."""
        cut = now - TASK_H * 3600
        try:
            with open(TURNS, "rb") as f:
                f.seek(self.turns_off)
                for line in f:
                    if not line.endswith(b"\n"):
                        break
                    self.turns_off += len(line)
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    t = d.get("task")
                    if not t or d.get("ts", 0) < cut or t in self.task_tpl:
                        continue
                    self.task_tpl[t] = (d["ts"], template(d.get("buyer"), self.names.get(d.get("sku"))))
        except FileNotFoundError:
            pass
        for t in [t for t, (ts, _) in self.task_tpl.items() if ts < cut]:
            del self.task_tpl[t]

    def _read_ledger(self, now):
        db = model.connect()
        try:
            if not self.names:
                cat = model.catalog(db)
                self.names = {s: r["name"] for s, r in cat.items()}
                self.goods = {s for s, r in cat.items() if r["kind"] == "goods"}
            self.ours = model.our_urls(db) or self.ours
            cut = now - max(WINDOW_H, TASK_H) * 3600
            if self.seq is None:
                row = db.execute("SELECT MIN(seq) FROM ledger WHERE kind='result' AND ts > ?", (cut,)).fetchone()
                self.seq = (row[0] or 1) - 1
            for seq, ts, kind, data in db.execute(
                    "SELECT seq, ts, kind, data FROM ledger WHERE seq > ? AND kind IN ('result', 'settle') "
                    "ORDER BY seq", (self.seq,)):
                self.seq = seq
                d = json.loads(data)
                if kind == "settle":
                    self.settled[d.get("order")] = ts
                    continue
                r = d.get("receipt") or {}
                if not r.get("claim") or r.get("item") not in self.goods:
                    continue
                task = r.get("task") if d.get("shop") in self.ours else None
                self.results.append((ts, d.get("order"), d.get("buyer"), r["claim"],
                                     bool(r.get("warranty_hours")), task))
        finally:
            db.close()
        while self.results and self.results[0][0] < cut:
            self.results.popleft()
        for o in [o for o, ts in self.settled.items() if ts < cut]:
            del self.settled[o]

    # ---------- building ----------
    def build(self, now=None):
        now = now or time.time()
        self._read_ledger(now)
        self._read_turns(now)
        wcut, tcut = now - WINDOW_H * 3600, now - TASK_H * 3600
        stats = collections.defaultdict(lambda: [0, 0])          # (buyer, grade, warranted) -> [paid, refused]
        tpl_b = collections.defaultdict(collections.Counter)      # template -> buyer -> our receipts
        for ts, order, buyer, grade, w, task in self.results:
            if ts >= wcut and ts < now - 120:                     # the last 2 min may still settle
                stats[(buyer, grade, w)][0 if order in self.settled else 1] += 1
            if task and ts >= tcut and task in self.task_tpl:
                tpl_b[self.task_tpl[task][1]][buyer] += 1
        table = {}
        for tpl, bs in tpl_b.items():
            n = sum(bs.values())
            if n < MIN_TPL_N:
                continue
            for g in ("A", "B", "C"):
                rate = {}
                for w in (False, True):
                    cov = acc = 0.0
                    for b, k in bs.items():
                        paid, ref = stats.get((b, g, w), (0, 0))
                        if paid + ref >= MIN_BUYER_N:
                            cov += k / n
                            acc += k / n * paid / (paid + ref)
                    rate[w] = (cov, acc / cov if cov else None)
                (c0, r0), (c1, r1) = rate[False], rate[True]
                dec = None
                if c0 >= FREE_COV and r0 >= FREE_RATE:
                    dec = 0
                elif c0 >= NEED_COV and r0 < NEED_RATE and not (c1 >= NEED_COV and r1 < NEED_W_RATE):
                    dec = int(policy.NUMBERS["warranty_hours"][0])
                table[(tpl, g)] = {"decision": dec, "n": n, "buyers": dict(bs.most_common(3)),
                                   "cov0": round(c0, 2), "rate0": None if r0 is None else round(r0, 3),
                                   "cov1": round(c1, 2), "rate1": None if r1 is None else round(r1, 3)}
        with self.lock:
            self.table = table
            self.ready = True
        return table

    def decide(self, tpl, grade):
        """0, 72 or None (not known: the seller's own rules)."""
        with self.lock:
            e = self.table.get((tpl, grade))
        return e["decision"] if e else None


MEMO = Memo()


def start(log=print):
    """Build in a daemon thread now and every REFRESH_S; until the first build every decision is None."""
    def loop():
        while True:
            try:
                t0 = time.time()
                tb = MEMO.build()
                log(f"[warranty_memo] {len(tb)} template-grade rows, "
                    f"{sum(1 for e in tb.values() if e['decision'] == 0)} without, "
                    f"{sum(1 for e in tb.values() if e['decision'])} with a warranty ({time.time() - t0:.0f} s)")
            except Exception as e:
                log(f"[warranty_memo] {type(e).__name__}: {str(e)[:160]}")
            time.sleep(REFRESH_S)
    threading.Thread(target=loop, name="warranty_memo", daemon=True).start()


if __name__ == "__main__":
    t0 = time.time()
    tb = MEMO.build()
    print(f"built in {time.time() - t0:.0f} s: {len(tb)} rows, tasks {len(MEMO.task_tpl)}, results {len(MEMO.results)}")
    rows = sorted(tb.items(), key=lambda x: -x[1]["n"])
    for (tpl, g), e in rows:
        if e["decision"] is not None or e["n"] >= 100:
            print(f"{g} {str(e['decision']):>4} n={e['n']:5d} w0 {e['cov0']:.2f}/{e['rate0']} "
                  f"w {e['cov1']:.2f}/{e['rate1']} {e['buyers']} | {tpl[:90]}")
    # replay: our receipts of the window, what the decision would have changed
    ch = collections.Counter()
    for ts, order, buyer, grade, w, task in MEMO.results:
        if not task or task not in MEMO.task_tpl or ts < time.time() - WINDOW_H * 3600:
            continue
        dec = MEMO.decide(MEMO.task_tpl[task][1], grade)
        if dec is None:
            continue
        paid = order in MEMO.settled
        ch[(grade, "had w" if w else "had 0", "-> 0" if dec == 0 else "-> w", "paid" if paid else "refused")] += 1
    print("\nour receipts in the window the table would change (grade, had, now, outcome then):")
    for k, v in sorted(ch.items()):
        print(" ", k, v)
