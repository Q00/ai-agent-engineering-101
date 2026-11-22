"""Recount the numbers REPORT.md quotes, from the copies under logs/ only.

    python report_numbers.py            everything (about a minute: the seller log is 0.5 GB unpacked)
    python report_numbers.py kit        one part: kit | market | decisions | strategist | forum

logs/market-kit*.log                 the kit's log and its archives (Round 3: the files from 20261107 on)
logs/market/                         the kit's own files from .market/: payments.jsonl (every checkout my buyer paid or
                                     did not, with the reason), broken.jsonl (what broke of its purchases), inventory.json
logs/decisions/<role>.jsonl.gz       every decision row of the seller, buyer, restock, review and forum hooks
logs/strategist-home/runs.jsonl      one row per strategist run; changes.jsonl one per SKILL.md rewrite or outcome check
logs/outbox.sent.jsonl               the forum posts the kit sent and the market's answers
Nothing is written. A row that does not parse (a line cut by the copy) is skipped.

Round 3 ended on 2026-11-17 at 23:59 KST (END_TS) and the agents kept running. Everything is counted up to the
end: decision rows and strategist runs by their ts, the kit log (its stamps have no date) up to the market's
first "Round 3 ended" answer. The board numbers are the ones the first strategist run after the end read.
"""
import collections
import gzip
import json
import re
import statistics
import sys
import time
from pathlib import Path

LOGS = Path(__file__).resolve().parent / "logs"
END_TS = 1794927540                                 # 2026-11-17 23:59 KST, stock.end_ts
WEEK10_TS = 1794304800                              # 2026-11-10 19:00 KST, the week-10 class (the lab's baseline)
KIT_FIRST = "market-kit.20261107-184911.log"        # the archive Round 3 starts in (the one before is Round 2)
KIT_END = "Round 3 ended at"
SELLER_WINDOW_H, BUYER_WINDOW_H = 12, 36


def rows(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def stamp(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def norm(text):
    text = re.sub(r"https?://\S+", "<url>", str(text or ""))
    text = re.sub(r"G[0-9a-f]{8}", "<sku>", text)
    return re.sub(r"\d+(\.\d+)?", "N", text).split("\n")[0][:80]


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else None


def kit():
    names = sorted(p.name for p in LOGS.glob("market-kit.2*.log") if p.name >= KIT_FIRST) + ["market-kit.log"]
    lines, ended = [], None
    for name in names:
        for line in (LOGS / name).read_text(encoding="utf-8", errors="replace").splitlines():
            if KIT_END in line:
                ended = f"{name} at {line[:8]}"
                break
            lines.append(line)
        if ended:
            break

    def uniq(marker):
        return len({m.group(0) for line in lines if marker in line for m in [re.search(r"ledger #\d+", line)] if m})

    def count(pattern):
        rx = re.compile(pattern)
        return sum(1 for line in lines if rx.search(line))

    print(f"== kit logs, Round 3: {names[0]} up to the first '{KIT_END}' line ({ended or 'not reached'})")
    print("shop settlements, unique ledger numbers:", uniq("[acp] settled "))
    print("buyer settlements, unique ledger numbers:", uniq("[acp] paid "))
    print("orders received (round 3):", count(r"\[order o-.*round 3"))
    print("checkout above the mandate's limit:", count(r"above the mandate's limit"))
    print("kit starts:", count(r"\[kit\] local patches"))
    for label, pattern in (
            ("not paying: shelf life", r"not paying: [0-9.]+ h of shelf"),
            ("not paying: no certificate", r"not paying: no certificate"),
            ("checkout refused by a shop (my buyer): unit not in stock", r"checkout refused: [0-9]+ .*is not in stock for this order"),
            ("checkout refused by my shop: unit not in stock", r"checkout refused: unit .*is not in stock for this order"),
            ("not paying: ACP endpoint not on the shop's host", r"is not on the shop's host; not paying"),
            ("payment not completed", r"payment not completed"),
            ("  empty market error", r'payment not completed: [0-9]+ .*"market":\{"error":""\}'),
            ("  Internal Server Error", r"payment not completed: .*Internal Server Error"),
            ("  settle checks failed", r"payment not completed: .*checks"),
            ("settle refused: not_settled_before", r"settle refused: \['not_settled_before'\]"),
            ("settle refused: unit_in_stock", r"settle refused: \['unit_in_stock'\]"),
            ("settle refused: market error", r"settle refused: \{"),
            ("forum post answered 200", r"\[forum\] post as buyer: 200")):
        print(f"{label}:", count(pattern))


def market():
    """The kit's working files: the buyer's payments (Round 3 only: the file starts with kit v4) and breakages."""
    folder = LOGS / "market"
    pays = list(rows(folder / "payments.jsonl"))
    paid = [r for r in pays if r.get("paid")]
    print("== .market files (copies in logs/market/)")
    print(f"payments.jsonl: {len(pays)} checkouts, paid {len(paid)} (unique ledger numbers "
          f"{len({r.get('ledger') for r in paid})}), not paid {len(pays) - len(paid)}")
    why = collections.Counter()
    for r in pays:
        if not r.get("paid"):
            w = re.sub(r"<!DOCTYPE.*", "<html error page>", str(r.get("reason")), flags=re.S)
            w = norm(re.sub(r"u-[0-9a-f]{12}", "<unit>", w))        # the unit id first: norm() would cut its digits
            why[w.split(',"checks"')[0][:95]] += 1
    for k, v in why.most_common():
        print(f"  {v:6d}  {k}")
    broke = [r.get("broke_at") or 0 for r in rows(folder / "broken.jsonl")]
    print(f"broken.jsonl: {len(broke)} purchases broke, {sum(1 for t in broke if t <= END_TS)} of them before the end")
    inv = json.loads((folder / "inventory.json").read_text(encoding="utf-8"))
    units = inv.get("units") or []
    print(f"inventory.json: balance {inv.get('balance')}, start {inv.get('start')}, bankruptcies {inv.get('bankruptcies')}, "
          f"units {len(units)}, stock at cost {sum(u.get('cost') or 0 for u in units)}")


def decisions():
    print("== decision logs")
    total = 0
    for role in ("seller", "buyer", "restock", "review", "forum"):
        path = LOGS / "decisions" / f"{role}.jsonl.gz"
        n, first, last = 0, None, None
        llm = collections.Counter()
        recent = collections.deque()            # (ts, row) of the window; the files are in time order
        window = {"seller": SELLER_WINDOW_H, "buyer": BUYER_WINDOW_H}.get(role)
        for row in rows(path):
            ts = row.get("ts") or 0
            if not 0 < ts <= END_TS:                # after the round's end, or a row without a time
                continue
            n += 1
            first, last = first or ts, ts
            if row.get("kind") == "llm_exception":
                llm[str(row.get("why"))] += 1
            if window:
                slim = row if role == "buyer" else {"kind": row.get("kind"), "elapsed_ms": row.get("elapsed_ms")}
                recent.append((ts, slim))
                while recent and recent[0][0] < ts - window * 3600:
                    recent.popleft()
        total += n
        print(f"{role}: {n} rows, {stamp(first)} to {stamp(last)}")
        if llm:
            print(f"  model fallback attempts {sum(llm.values())}:", dict(llm.most_common()))
        if role == "seller":
            ms = [r["elapsed_ms"] for _, r in recent if r["kind"] != "llm_exception" and r["elapsed_ms"] is not None]
            print(f"  last {window} h from {stamp(recent[0][0])}: {len(recent)} rows, "
                  f"decision time median {statistics.median(ms)} ms, p90 {pct(ms, 0.9)} ms")
        if role == "buyer":
            orders = [r for _, r in recent if r.get("kind") == "order"]
            deals = [r for r in orders if isinstance(r.get("choice"), dict) and r["choice"].get("shop")]
            sec = [r["elapsed_ms"] / 1000 for r in orders if r.get("elapsed_ms")]
            print(f"  last {window} h from {stamp(recent[0][0])}: {len(orders)} orders, {len(deals)} deals, "
                  f"order time median {statistics.median(sec):.1f} s, p90 {pct(sec, 0.9):.1f} s")
            why, problem = collections.Counter(), collections.Counter()
            for r in orders:
                for t in r.get("tried") or []:
                    w = norm(t.get("why"))
                    w = "timeout" if w.startswith("ReadTimeout") else "time gate" if w.startswith("past N s") else \
                        "http error" if w.startswith("HTTPStatusError") else w
                    why[w] += 1
                    if t.get("receipt_problem"):
                        p = norm(t["receipt_problem"])
                        problem["Saved check" if p.startswith("Saved check") else p] += 1
            for name, counter in (("tries by how they ended", why), ("receipts refused", problem)):
                print(f"  {name}:")
                for k, v in counter.most_common(9):
                    print(f"    {v:6d}  {k}")
    print("all decision rows:", total)


def strategist():
    home = LOGS / "strategist-home"
    every = list(rows(home / "runs.jsonl"))
    runs = [r for r in every if r.get("ts", 0) <= END_TS]
    print("== strategist, runs up to the round's end")
    print(f"runs: {len(runs)}, {runs[0]['run_id']} to {runs[-1]['run_id']}, policy version now {runs[-1].get('policy_ver_after')}")
    status = collections.Counter("waiting for an outcome" if str(r.get("status")).startswith("waiting") else r.get("status")
                                 for r in runs)
    print("status:", dict(status.most_common()))
    source, keys, refused = collections.Counter(), collections.Counter(), collections.Counter()
    for r in runs:
        for a in r.get("applied") or []:
            # rows of 11-04 to 11-06 carry no source: the field came later, and all of them are model changes
            source[a.get("source") or "model, before the source field"] += 1
            if a.get("source") in (None, "model"):
                keys[a.get("key")] += 1
        for a in r.get("rejected") or []:
            w = str(a.get("why") or a.get("reason") or "")
            refused["confidence" if w.startswith("confidence") else w.split(":")[0][:20]] += 1
    print("applied rows by source:", dict(source.most_common()))
    print(f"model changes applied: {sum(keys.values())}, most moved keys:", keys.most_common(4))
    print("guard refusals:", dict(refused.most_common()))
    print("runs where the model listed tool calls:", sum(1 for r in runs if r.get("tool_calls")))
    analyst = [r["analyst"]["accepted"] for r in runs if (r.get("analyst") or {}).get("accepted")]
    print(f"analyst: {len(analyst)} runs wrote {sum(len(a) for a in analyst)} multipliers")
    ms = [r["elapsed_ms"] for r in runs if r.get("elapsed_ms")]
    print(f"run time median {statistics.median(ms) / 1000:.0f} s")
    changes = [c for c in rows(home / "changes.jsonl") if c.get("ts", 0) <= END_TS]
    checks = [c for c in changes if "outcome_of" in c]
    print(f"outcome checks: {len(checks)}, automatic reverts: {sum(1 for c in checks if c.get('reverted') or c.get('revert'))}")
    lists = sum(1 for c in changes if any(k in ("stock.focus_skus", "stock.trend_skus") for k in (c.get("diff") or {})))
    print("SKILL.md rewrites that changed the focus or trend list:", lists)
    after = [r for r in every if r.get("ts", 0) > END_TS]
    print(f"runs after the end (market paused): {len(after)}")
    week10 = min(runs, key=lambda r: abs(r.get("ts", 0) - WEEK10_TS))
    for label, run in (("week-10 class, the baseline", week10), ("last read before the end", runs[-1]),
                       ("first read after the end", after[0] if after else None), ("latest read", every[-1])):
        if run is None:
            continue
        me = run.get("me") or {}
        shop, buyer, player = me.get("shop") or {}, me.get("buyer") or {}, me.get("player") or {}
        brief = home / "briefs" / f"{run['run_id']}.json"
        cls = ((json.loads(brief.read_text(encoding="utf-8")).get("score") or {}).get("class") or {}) if brief.is_file() else {}
        print(f"board, {label} ({run['run_id']}):")
        print(f"  class rank {cls.get('rank', '?')} of {cls.get('of', '?')}, student total {player.get('student_total')} "
              f"(shop {player.get('student_shop')} + buyer {player.get('student_buyer')}), percentile {player.get('student_pct')}; "
              f"all players rank {player.get('total_rank')} of {player.get('ranked')}, total {player.get('total')}")
        print(f"  shop: ALL {shop.get('all')} rank {shop.get('all_rank')} of {shop.get('ranked')}, deals {shop.get('deals')} "
              f"(rank {shop.get('deals_rank')}), revenue {shop.get('revenue')}, stars {shop.get('stars')}, "
              f"honest claims {shop.get('honest_claims')} of {shop.get('claims')}, broken {shop.get('broken')}, refunds {shop.get('refunds')}")
        print(f"  buyer: ALL {buyer.get('all')} rank {buyer.get('all_rank')} of {buyer.get('ranked')}, orders {buyer.get('orders')}, "
              f"deals {buyer.get('deals')}, saved {buyer.get('saved')}, forum {buyer.get('forum')}, posts {buyer.get('posts')}")
        for part, v in sorted((cls.get("parts") or {}).items()):
            print(f"    class {part}: {v.get('value')} rank {v.get('rank')}")


def forum():
    kinds = collections.Counter()
    for row in rows(LOGS / "outbox.sent.jsonl"):
        post = (row.get("sent") or {}).get("post") or ""
        kind = "deal post" if post.startswith("[구매 기록]") else "price report" if post.startswith("[시세]") else "other"
        kinds[(kind, row.get("status"))] += 1
    print("== forum posts sent (kind, market answer):", dict(sorted(kinds.items(), key=str)))


if __name__ == "__main__":
    parts = {"kit": kit, "market": market, "decisions": decisions, "strategist": strategist, "forum": forum}
    for name in sys.argv[1:] or parts:
        parts[name]()
