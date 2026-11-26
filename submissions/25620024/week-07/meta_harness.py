"""Week 08 — meta_harness.py: the buyer's outer loop. Runs every hour (a systemd timer), outside the buyer, so the
buyer's own (contaminated) context never judges itself.

One cycle:
  1. measure the trades since the current theta took effect: J = surplus per order, the buyer's KPI
     (sum of (budget - paid) / budget over deals, divided by all orders, so a lost deal counts as 0)
  2. decide, as an A/B in time: a baseline cycle re-measures the best numbers so far and then tries one
     knob one step; a trial cycle keeps the step if J beat the baseline by TOL, otherwise goes back
  3. andon (Toyota): a receipt above the agreed price, or a deal rate under ANDON_DEALS, stops tuning;
     the best numbers stay until a person looks
  4. write theta.json: the numbers, plus a few rules built from facts in memory (market lows, paused and
     banned shops) that go first in every buyer prompt. Rules are rebuilt whole but only from facts,
     and theta changes once per cycle, so prompts stay the same for an hour at a time.

Every version is kept in theta/vNNN.json with the metrics it earned; every cycle adds a line to
logs/meta-harness.jsonl.

    python meta_harness.py            # one cycle
    python meta_harness.py --dry      # measure and print, write nothing
"""
import argparse
import json
import statistics
import time
from pathlib import Path

import buyer_memory

HERE = Path(__file__).parent
THETA = buyer_memory.THETA
ARCHIVE = THETA.parent / "theta"
LOG = THETA.parent / "logs" / "meta-harness.jsonl"
MIN_TRADES = 30            # fewer trades than this since the last version: wait for the next cycle
TOL = 0.01                 # a trial must beat the baseline J by this much to be kept (windows of the same numbers differed by ~0.01 in a replay)
ANDON_DEALS = 0.30         # deal rate under this stops tuning
KNOBS = [                  # (name, low, high, step): one knob, one step per trial
    ("concede", 0.2, 0.6, 0.1),
    ("take_after", 1, 4, 1),
    ("first_offer_max", 0.5, 0.8, 0.05),
]


def trades_since(since):
    out = []
    with buyer_memory.MEMORY.open(encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if (m.get("ts") or 0) >= since and m.get("shop") and m.get("budget"):
                out.append(m)
    return out


def measure(trades, state):
    deals = [m for m in trades if m.get("outcome") == "deal" and m.get("agreed")]
    over = []
    for m in deals:
        low = buyer_memory.market_low(state, m.get("item"))
        if low:
            over.append((m["agreed"] - low) / low)
    return {"orders": len(trades), "deals": len(deals),
            "deal_rate": round(len(deals) / len(trades), 3),
            "J": round(sum((m["budget"] - m["agreed"]) / m["budget"] for m in deals) / len(trades), 4),
            "overpay_median": round(statistics.median(over), 4) if over else None,
            "severe": sum(1 for m in trades if buyer_memory.severity(m.get("problem")) == "severe")}


def rules(state, metrics):
    """A few lines of fact for the front of the buyer's prompts: where the floor is, who to avoid."""
    lows = {i: buyer_memory.market_low(state, i) for i in sorted(state.get("items") or {})}
    lows = {i: v for i, v in lows.items() if v}
    out = []
    if lows:
        out.append("Market lows (no shop signs below them): " + ", ".join(f"{i} {v}" for i, v in lows.items())
                   + ". Offer the low first and come up slowly; every credit above it is surplus lost.")
    if metrics.get("overpay_median"):
        out.append(f"Last window I paid a median {metrics['overpay_median']:.1%} above the market low.")
    held = []
    for name, p in state["profiles"].items():
        level, _ = buyer_memory.sanction(p)
        if level:
            held.append(f"{name} ({level})")
    if held:
        out.append("Shops I avoid for cheating: " + ", ".join(sorted(held)) + ".")
    return out


def step(numbers, knob, direction):
    name, low, high, size = KNOBS[knob]
    value = round(numbers[name] + direction * size, 3)
    if not low <= value <= high:
        return None
    return {**numbers, name: int(value) if isinstance(numbers[name], int) else value}


def propose(best, meta):
    """The next trial from the best numbers: the current knob in the current direction; if that leaves
    the bounds, the other direction; then the next knob."""
    for _ in range(2 * len(KNOBS)):
        trial = step(best, meta["knob"], meta["dir"])
        if trial:
            return trial
        meta["tried"] = meta.get("tried", 0) + 1
        if meta["dir"] == 1:
            meta["dir"] = -1
        else:
            meta["dir"], meta["knob"] = 1, (meta["knob"] + 1) % len(KNOBS)
    return None


def cycle(dry=False):
    now = time.time()
    try:
        th = json.loads(THETA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tunable = {k: buyer_memory.DEFAULTS[k] for k, *_ in KNOBS}
        th = {"version": 1, "since": now, "phase": "baseline", "buyer": tunable,
              "best": {"buyer": tunable, "J": None}, "meta": {"knob": 0, "dir": 1}, "rules": [],
              "why": "first version: the defaults", "written": time.strftime("%Y-%m-%d %H:%M:%S")}
        print(f"no theta yet: v1 with the defaults {tunable}")
        if not dry:
            THETA.write_text(json.dumps(th, indent=1, ensure_ascii=False), encoding="utf-8")
        return th
    trades = trades_since(th["since"])
    if len(trades) < MIN_TRADES:
        print(f"v{th['version']}: {len(trades)} trades since it started, waiting for {MIN_TRADES}")
        return th
    state = buyer_memory.load(full=True)
    m = measure(trades, state)
    best, meta, phase = th["best"], th["meta"], th["phase"]
    name = KNOBS[meta["knob"]][0]

    if m["severe"] or m["deal_rate"] < ANDON_DEALS:
        decision = f"andon: {m['severe']} inflated receipts, deal rate {m['deal_rate']:.0%}; tuning stopped, best numbers kept"
        numbers, phase = best["buyer"], "baseline"
    elif phase == "baseline":
        best["J"] = m["J"] if best["J"] is None else round((best["J"] + m["J"]) / 2, 4)
        numbers = propose(best["buyer"], meta)
        name = KNOBS[meta["knob"]][0]
        if numbers:
            decision = f"baseline J {m['J']} (best now {best['J']}); trying {name} {best['buyer'][name]} -> {numbers[name]}"
            phase = "trial"
        else:
            decision, numbers = f"baseline J {m['J']}; no knob can move", best["buyer"]
    else:
        if best["J"] is None or m["J"] > best["J"] + TOL:
            decision = f"kept {name} = {th['buyer'][name]}: J {m['J']} beat {best['J']}"
            best["buyer"], best["J"] = th["buyer"], m["J"]
        else:
            decision = f"undid {name} = {th['buyer'][name]}: J {m['J']} did not beat {best['J']}"
            if meta["dir"] == 1:
                meta["dir"] = -1
            else:
                meta["dir"], meta["knob"] = 1, (meta["knob"] + 1) % len(KNOBS)
        numbers, phase = best["buyer"], "baseline"

    th["metrics"] = m
    new = {"version": th["version"] + 1, "since": now, "phase": phase, "buyer": numbers, "best": best,
           "meta": meta, "rules": rules(state, m), "why": decision, "previous_metrics": m,
           "written": time.strftime("%Y-%m-%d %H:%M:%S")}
    print(f"v{th['version']} {m}\n  -> v{new['version']} {phase}: {decision}\n  numbers {numbers}\n  rules {new['rules']}")
    if dry:
        return new
    ARCHIVE.mkdir(exist_ok=True)
    (ARCHIVE / f"v{th['version']:03d}.json").write_text(json.dumps(th, indent=1, ensure_ascii=False), encoding="utf-8")
    THETA.write_text(json.dumps(new, indent=1, ensure_ascii=False), encoding="utf-8")
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": round(now, 1), "from": th["version"], "to": new["version"], "metrics": m,
                            "decision": decision, "numbers": numbers}, ensure_ascii=False) + "\n")
    return new


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    cycle(ap.parse_args().dry)
