"""Round 3 A/B test inside one shop: two values of one strategy number, drawn per negotiation at the same hours.

    python ab_r3.py            # prints the running experiment's numbers per arm

Why: the meta-harness changes a number and compares the next 15 minutes with the last, but the market's traffic
moves far more than that between hours (on 11/3, 8 deals in a night half hour, 73 in a late morning one), so a
before/after comparison mostly measures the clock. Here both values run at the same time and each negotiation
draws one, so the hour cancels out.

experiment_r3.json (written by hand or by meta_r3.py; "on": false keeps the seller on strategy_r3.json alone):
    {"on": true, "id": "ask-goods-1", "param": "ask_frac", "kind": "goods",
     "arms": {"A": 0.95, "B": 0.85}, "min_n": 30, "p_win": 0.95}
The draw is Thompson sampling (a two-armed bandit): the arm that earns more gets more negotiations as evidence
comes in, so a worse value costs fewer sales than a fixed 50/50 split; each arm gets its first `FIRST` negotiations
regardless. The reward of a negotiation is its margin as a share of list price, (price - cost) / list, and 0 for a
walk-away or a buyer that left: profit is half of the shop's score, and a lost sale earns nothing. Both arms keep the
seller's floor (cost plus min_margin), so neither can sell at a loss.
Events go to logs/ab_r3.jsonl (append-only, one line each), which is also what the numbers are rebuilt from.
"""
import json
import math
import os
import random
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AB_DIR = Path(os.environ.get("AB_DIR") or HERE)       # tests point it at a temporary folder
EXPERIMENT = AB_DIR / "experiment_r3.json"
LOG = AB_DIR / "logs" / "ab_r3.jsonl"
FIRST = 10              # negotiations each arm gets before the bandit leans
# an experiment with no winner ends as a tie once both arms have MAX_N negotiations or it has run MAX_HOURS (11/4:
# ask_frac_food 0.92 against 0.90 ran 20 h and 4,193 negotiations without a winner, and while it ran no other test
# could start); a tie keeps the strategy's value. 11/4 22:00: two tests in 25 hours, both ties, while 13 days remain
# and every other test waits for the one slot; a difference too small to show in 250 talks an arm or 2 hours is too
# small to matter next to the gaps on the board
MAX_N = 250
MAX_HOURS = 2
PRIOR = (0.10, 2)       # prior mean reward and its weight in negotiations (a 10% margin on list, weakly held)
SD_MIN = 0.05


def experiment():
    try:
        e = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = (e.get("on") is True and isinstance(e.get("param"), str) and isinstance(e.get("arms"), dict)
          and len(e["arms"]) == 2 and all(isinstance(v, (int, float)) or (isinstance(v, dict) and v and all(
              isinstance(x, (int, float)) for x in v.values())) for v in e["arms"].values()))   # a number or a playbook
    return e if ok else None


def record(**event):
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(dict(event, t=round(time.time(), 1)), ensure_ascii=False) + "\n")


def paid_rewards(exp_id):
    """arm -> rewards counting only what the ledger paid (an experiment with "reward": "paid", 11/9): a talk that ended
    in a deal earns (price - cost) / list + RATIO * price / list when our shop's settle of that SKU at that price follows
    within 15 minutes, else 0; a walk or a buyer that left earns 0. RATIO is the board's price of revenue over that of
    Book (metrics_r3.lever_prices), so the reward is the shop's board points per talk in Book units. Until 11/9 the
    reward was margin at agreement, and a playbook that won on it cut paid sales 65% (06:43-09:21)."""
    try:
        import metrics_r3
        p = metrics_r3.lever_prices() or {}
    except Exception:
        p = {}
    ratio = p["revenue"] / p["book"] if p.get("book") and p.get("revenue") else 0.15
    try:
        entries = json.loads((AB_DIR / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        entries = []
    ours = {(e.get("data") or {}).get("url", "").rstrip("/") for e in entries if e.get("kind") == "join"
            and (e.get("data") or {}).get("name") == "thisjonghoon" and (e.get("data") or {}).get("role") == "shop"}
    paid = {}                           # (sku, amount) -> settle times; each settle is used by one talk only
    for e in entries:
        d = e.get("data") or {}
        if e.get("kind") == "settle" and str(d.get("shop") or "").rstrip("/") in ours:
            paid.setdefault((d.get("sku") or d.get("item"), d.get("amount")), []).append(e.get("ts", 0))
    out = {}
    try:
        talks = (AB_DIR / "logs" / "talks_r3.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        talks = []
    for line in talks:
        try:
            x = json.loads(line)
        except ValueError:
            continue
        ab = x.get("ab")
        if not ab or ab[0] != exp_id:
            continue
        if x.get("event") == "deal" and x.get("price") and x.get("list_price"):
            if time.time() - x["t"] < 900:
                continue                    # its settle may still come: not counted yet, rather than counted as 0
            ts_list = paid.get((x.get("sku"), x["price"]), [])
            hit = next((ts for ts in ts_list if x["t"] - 60 <= ts <= x["t"] + 900), None)
            if hit is not None:
                ts_list.remove(hit)
            # in won, per 100,000 (11/9 review): over list price a 30k sale weighed what a 600k one did, while Book
            # grows in won; the prior's 0.10 is then a 10,000-won point
            r = ((x["price"] - (x.get("cost") or 0)) + ratio * x["price"]) / 100_000 if hit is not None else 0.0
        elif x.get("event") in ("walk", "gone"):
            r = 0.0
        else:
            continue
        out.setdefault(ab[1], []).append(round(r, 4))
    return out


def rewards(exp_id):
    """arm -> list of rewards of the negotiations of experiment `exp_id` that have ended (open ones are left out)."""
    e = experiment()
    if e and e.get("id") == exp_id and e.get("reward") == "paid":
        return paid_rewards(exp_id)
    out, arm_of = {}, {}
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("id") != exp_id:
            continue
        if e.get("event") == "open":
            arm_of[e.get("task")] = e.get("arm")
        elif e.get("event") in ("deal", "walk", "gone") and e.get("task") in arm_of:
            out.setdefault(arm_of.pop(e["task"]), []).append(e.get("reward") or 0.0)
    return out


def posterior(r):
    """(mean, sd of the mean) of an arm's reward, with the weak prior."""
    m0, w = PRIOR
    n = len(r)
    mean = (sum(r) + m0 * w) / (n + w)
    var = sum((x - mean) ** 2 for x in r) / (n - 1) if n > 1 else SD_MIN ** 2
    return mean, max(math.sqrt(var), SD_MIN) / math.sqrt(n + w)


def choose(kind):
    """(experiment, arm) for a new negotiation of a unit of `kind`, or (None, None) when no experiment applies."""
    e = experiment()
    if not e or e.get("kind", "all") not in ("all", kind):
        return None, None
    r = rewards(e["id"])
    arms = list(e["arms"])
    # both arms get min_n negotiations before the bandit leans (11/4 23:00: after FIRST = 10 each, arm A drew 208 and
    # arm B 18, so B could never reach min_n and the test could only end as a tie)
    short = [a for a in arms if len(r.get(a, [])) < max(FIRST, e.get("min_n", 30))]
    if short:
        return e, min(short, key=lambda a: len(r.get(a, [])))
    draw = {a: random.gauss(*posterior(r.get(a, []))) for a in arms}
    return e, max(draw, key=draw.get)


def summary(e=None):
    """Per arm: negotiations ended, deals, mean reward, and the chance each arm is the better one."""
    e = e or experiment()
    if not e:
        return None
    r = rewards(e["id"])
    (a, b) = list(e["arms"])
    (ma, sa), (mb, sb) = posterior(r.get(a, [])), posterior(r.get(b, []))
    z = (mb - ma) / math.sqrt(sa ** 2 + sb ** 2)
    p_b = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    arm = lambda k, m: {"value": e["arms"][k], "n": len(r.get(k, [])), "deals": sum(x > 0 for x in r.get(k, [])),
                        "mean_reward": round(m, 4)}
    return {"id": e["id"], "param": e["param"], "kind": e.get("kind", "all"), a: arm(a, ma), b: arm(b, mb),
            f"p_{b}_better": round(p_b, 3)}


def winner(e=None):
    """The arm that is better with probability p_win once both have min_n negotiations, else None."""
    e = e or experiment()
    s = summary(e)
    if not s:
        return None
    (a, b) = list(e["arms"])
    if min(s[a]["n"], s[b]["n"]) < e.get("min_n", 30):
        return None
    p = s[f"p_{b}_better"]
    win = b if p >= e.get("p_win", 0.95) else a if 1 - p >= e.get("p_win", 0.95) else None
    if win is None and (min(s[a]["n"], s[b]["n"]) >= MAX_N or time.time() - e.get("started", time.time()) > MAX_HOURS * 3600):
        return "tie"
    return win


if __name__ == "__main__":
    print(json.dumps(summary(), ensure_ascii=False, indent=1) if experiment() else "no experiment on")
