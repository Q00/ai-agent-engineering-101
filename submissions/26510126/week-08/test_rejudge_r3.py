"""Tests for rejudge_r3: past number verdicts re-judged on the market share, or marked UNVERIFIED when no data
reaches back. The ledger cache, the verdict ledger and the output live in a temp folder; no model is called."""
import json
import tempfile
import time
from pathlib import Path

import metrics_r3
import rejudge_r3

RESULTS = []


def case(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))


def main():
    base = Path(tempfile.mkdtemp(prefix="rejudge-"))
    (base / ".market").mkdir()
    (base / "logs").mkdir()
    now = time.time()
    t = now - 3 * 3600                                   # a change 3 h ago: its 2 h after have run
    url = "https://ours.example"
    sale = lambda ts, shop, amount, u: {"kind": "settle", "ts": ts, "data": {"shop": shop, "amount": amount, "unit": u}}
    entries = [{"kind": "join", "ts": 0, "data": {"name": metrics_r3.ME, "role": "shop", "url": url}},
               sale(now - 6 * 3600, "https://x.example", 1, "u-0"),            # the cache reaches back 6 h
               sale(t - 3000, url, 100, "u-1"), sale(t - 2000, "https://x.example", 900, "u-2"),
               sale(t + 1000, url, 300, "u-3"), sale(t + 2000, "https://x.example", 700, "u-4")]
    (base / ".market" / "ledger-recent.json").write_text(json.dumps(entries))
    rows = [{"t": now - 30 * 3600, "source": "inner loop (number)", "change": "dear_depth 4 -> 5", "measured": 0.12},
            {"t": t, "source": "inner loop (number)", "change": "dear_depth 5 -> 4, max_list 900000 -> 600000",
             "measured": -0.1},
            {"t": now - 600, "source": "inner loop (number)", "change": "ask_frac 0.7 -> 0.75"},
            {"t": t, "source": "inner loop (number)", "change": "fast guard revert: x 1 -> 0"}]
    (base / "logs" / "code_changes_r3.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    added = rejudge_r3.update(base, now)
    done = json.loads((base / rejudge_r3.OUT).read_text())
    old, new = done.get(rejudge_r3.key(rows[0])), done.get(rejudge_r3.key(rows[1]))
    case("a verdict no data reaches back to is UNVERIFIED", old and old["status"] == "unverified", old)
    case("a raw -10% that the market share says rose is reversed", new and new["status"] == "reversed"
         and new["share"] == 2.0, new)
    case("a change whose 2 h have not run waits, a fast guard revert is no verdict", added == 2, added)
    case("a second pass adds nothing", rejudge_r3.update(base, now) == 0)
    # data that reaches back later (the backfill's buckets) turns an UNVERIFIED row into a re-judged one
    old_t = rows[0]["t"]
    with (base / metrics_r3.BUCKETS).open("w") as f:     # a live bucket first, the backfilled older ones after it
        f.write(json.dumps({"t": int(now) // 600 * 600 - 600, "ours_n": 0, "ours_value": 0, "ours_margin": 0,
                            "mkt_n": 1, "mkt_value": 1}) + "\n")
        for i, b0 in enumerate(range(int(old_t - 2 * 3600) // 600 * 600 - 600, int(old_t + 2 * 3600) + 600, 600)):
            after = b0 >= old_t
            f.write(json.dumps({"t": b0, "ours_n": 1, "ours_value": 30 if after else 10, "ours_margin": 3,
                                "mkt_n": 10, "mkt_value": 1000}) + "\n")
    again = rejudge_r3.update(base, now)
    redo = json.loads((base / rejudge_r3.OUT).read_text()).get(rejudge_r3.key(rows[0]))
    case("an UNVERIFIED row is re-judged once buckets reach back to it", again == 1 and redo["status"] != "unverified",
         (again, redo))
    text = rejudge_r3.memory_text(base)
    case("the memory names each number with its status, newest numbers first",
         "- dear_depth (" in text and "VERIFIED: this change raised" in text and "the raw verdict said the opposite" in text
         and text.index("- dear_depth") < text.index("- max_list") if "- max_list" in text else False, text)
    case("the memory tells the team UNVERIFIED rows are weak evidence", "weak evidence" in text)
    (base / "strategy_r3.json").write_text(json.dumps({"dear_depth": 5}))
    case("each number shows its value now", "- dear_depth (now 5):" in rejudge_r3.memory_text(base))
    case("the memory stays under its cap", len(rejudge_r3.memory_text(base, max_chars=300)) <= 300)
    # a change with another change inside its hours is MIXED, not a verdict (11/9 review: 87 of 90 windows held one)
    r1 = {"t": t, "source": "inner loop (number)", "change": "sku_depth 3 -> 5"}
    r2 = {"t": t + 1800, "source": "supervisor", "change": "stock_value 60M -> 70M"}
    mixed = rejudge_r3.judge(r1, base, [r1, r2])
    case("a verdict with another change in its hours is MIXED and names it", mixed and mixed["status"] == "mixed"
         and "stock_value" in rejudge_r3.note(mixed), mixed)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    return all(RESULTS)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
