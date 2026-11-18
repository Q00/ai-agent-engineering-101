"""Tests for lessons_r3: the facts each area's lessons rest on, the check that drops lessons citing unknown facts, the
history kept on a rewrite, and what the specialist reads. The model is a stub; files live in a temp folder."""
import asyncio
import json
import tempfile
import time
from pathlib import Path

import lessons_r3 as ls
import rejudge_r3

RESULTS = []


def case(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))


def main():
    base = Path(tempfile.mkdtemp(prefix="lessons-"))
    (base / "logs").mkdir()
    t = 1794100000
    rows = [{"t": t, "source": "inner loop (number)", "change": "dear_depth 5 -> 4", "measured": -0.39},
            {"t": t + 10, "source": "inner loop (number)", "change": "ask_frac 0.7 -> 0.75", "measured": 0.1},
            {"t": t + 20, "source": "meta (auto)", "change": "In `restock_r3.py`, the heat cap goes to 5x",
             "files": ["restock_r3.py"], "verdict": "reverted (shop_margin_h -20%)"},
            {"t": t + 30, "source": "supervisor", "change": "restore 4d10151: hot 300k+ lines stocked up to 5x again"},
            {"t": t + 40, "source": "inner loop (buyer A/B)", "change": "buyer CAP 0.8 -> 0.75", "verdict": "kept"},
            {"t": t + 50, "source": "inner loop (number)", "change": "fast guard revert: dear_depth 4 -> 5"}]
    (base / rejudge_r3.CHANGES).write_text("".join(json.dumps(r) + "\n" for r in rows))
    (base / rejudge_r3.OUT).write_text(json.dumps({rejudge_r3.key(rows[0]): {
        "status": "confirmed", "aim": -0.2, "share": 0.16, "net_pts": -0.2, "orig": -0.39, "sales": "54/5304 -> 44/5273"}}))

    merch, sales, buying = ls.facts("merch", base), ls.facts("sales", base), ls.facts("buying", base)
    mt = " ".join(x[2] for x in merch)
    case("merchandising gets its numbers, its code changes and the supervisor's restore", len(merch) == 3
         and "dear_depth" in mt and "heat cap" in mt and "restore 4d10151" in mt, [x[2][:60] for x in merch])
    case("a verified number says what the change did", "lowered shop points/h net of the market by 20%" in mt, mt)
    case("an old before/after code verdict is marked weak", "raw before/after" in mt, mt)
    case("sales gets ask_frac only, buying its A/B, no area gets a fast guard revert",
         len(sales) == 1 and "ask_frac" in sales[0][2] and len(buying) == 1 and "fast guard" not in mt)

    ok = [{"rule": "r1", "evidence": ["F1"], "status": "verified", "trap": "t"},
          {"rule": "r2", "evidence": ["F9"], "status": "verified"},          # an unknown fact
          {"rule": "r3", "evidence": [], "status": "verified"},              # no fact
          {"rule": "r4", "evidence": ["F2"], "status": "maybe"}]             # unknown status
    case("only lessons citing known facts with a known status are kept",
         [x["rule"] for x in ls.valid(ok, {"F1", "F2", "F3"})] == ["r1"])

    calls = []

    async def ask(prompt, **k):
        if prompt.startswith("Check each lesson"):
            return {"pass": [0]}
        calls.append(prompt)
        return {"lessons": [{"rule": f"rule {len(calls)}", "evidence": ["F1", "F3"], "status": "verified",
                             "trap": "F2 went -20%"}]}
    async def check_fails(prompt, **k):
        if prompt.startswith("Check each lesson"):
            return {"pass": [1], "fail": {"0": "reads a MIXED fact as a verdict"}}
        return {"lessons": [{"rule": "bad", "evidence": ["F1"], "status": "verified", "trap": ""},
                            {"rule": "good", "evidence": ["F3"], "status": "verified", "trap": ""}]}
    import shutil
    b2 = Path(tempfile.mkdtemp())
    shutil.copytree(base / "logs", b2 / "logs")
    asyncio.run(ls.dream("merch", b2, ask=check_fails, now=t + 50))
    got = ls.load(b2).get("merch") or {}
    case("a lesson the second reading fails is dropped, its reason kept",
         [x["rule"] for x in got.get("lessons") or []] == ["good"] and "0" in (got.get("failed_check") or {}), got)
    g = ls.grounded({"rule": "r", "evidence": ["F1"], "status": "verified"}, {"F1": "number x 1 -> 2: MIXED, no verdict"})
    case("a 'verified' lesson resting only on MIXED facts is downgraded", g["status"] == "unverified", g)
    n1 = asyncio.run(ls.dream("merch", base, ask=ask, now=t + 100))
    n2 = asyncio.run(ls.dream("merch", base, ask=ask, now=t + 200))
    cur = ls.load(base)["merch"]
    case("a rewrite keeps the old version in the history", n1 == 1 and n2 == 1 and cur["lessons"][0]["rule"] == "rule 2"
         and cur["history"][0]["lessons"][0]["rule"] == "rule 1", cur)
    case("the prompt shows the facts and the lessons now", "F1:" in calls[1] and "rule 1" in calls[1])
    case("a lesson carries its facts' times, and ids in its words become times",
         len(cur["lessons"][0]["when"]) == 2 and cur["lessons"][0]["trap"].startswith(ls.stamp(t + 20)), cur["lessons"][0])
    async def nothing(prompt, **k):
        return {"lessons": [{"rule": "x", "evidence": ["F99"], "status": "verified"}]}
    case("a dream with nothing valid leaves the lessons alone",
         asyncio.run(ls.dream("merch", base, ask=nothing, now=t + 300)) is None and ls.load(base)["merch"]["t"] == t + 200)
    async def racing(prompt, **k):                       # another writer saves community while this one waits
        d = ls.load(base)
        d["community"] = {"t": t + 400, "lessons": [{"rule": "c", "evidence": ["F1"], "status": "verified"}]}
        (base / ls.OUT).write_text(json.dumps(d))
        return {"lessons": [{"rule": "rule 3", "evidence": ["F1"], "status": "verified"}]}
    asyncio.run(ls.dream("merch", base, ask=racing, now=t + 400))
    case("a rewrite keeps another area saved while it ran", ls.load(base).get("community", {}).get("lessons"))
    txt = ls.text("merch", base)
    case("the specialist reads its lessons with status and times", "[verified] rule 3" in txt and len(txt) <= ls.MAX_CHARS)
    case("an area never dreamed reads nothing", ls.text("buying", base) == "")
    case("the most overdue area is dreamed next", ls.due(base, now=t + 200 + ls.DREAM_EVERY + 1) in
         ("sales", "buying", "community") and ls.due(base, now=t + 200) in ("sales", "buying", "community"))
    # memory evaluation: lesson use is recorded per answer and matched to the number changes that followed
    eb = Path(tempfile.mkdtemp()); (eb / "logs").mkdir()
    t0 = 1794200000
    ls.record_use({"sales": {"proposal": {"step": 0.5}, "lessons_used": ["L2"]},
                   "merch": {"proposal": {"dear_depth": 5}, "lessons_used": []}}, base=eb, now=t0,
                  shown={"sales": True, "merch": False})
    rows = [{"t": t0 + 60, "source": "inner loop (number)", "change": "step 0.4 -> 0.5"},
            {"t": t0 + 90, "source": "inner loop (number)", "change": "dear_depth 4 -> 5"}]
    (eb / rejudge_r3.CHANGES).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    (eb / rejudge_r3.OUT).write_text(json.dumps({rejudge_r3.key(rows[0]): {"status": "mixed"}}), encoding="utf-8")
    ev = ls.evaluate(eb, now=t0 + 100)
    case("lesson use is recorded and matched to the changes that followed",
         ev["sales"] == {"answers": 1, "hidden": 0, "named_a_lesson": 1, "changes": {"shown": {"mixed": 1}, "hidden": {}}}
         and ev["merch"]["hidden"] == 1 and ev["merch"]["changes"]["hidden"] == {"pending": 1}, ev)
    hid = [ls.shown_now(t0 + i * 900) for i in range(400)]
    case("lessons are hidden in about a quarter of an area's cycles",
         all(0.15 < 1 - sum(x[a] for x in hid) / 400 < 0.35 for a in ls.TITLES), hid[:3])

    async def all_fail(prompt, **k):
        if prompt.startswith("Check each lesson"):
            return {"pass": [], "fail": {"0": "no"}}
        return {"lessons": [{"rule": "wrong", "evidence": ["F1"], "status": "verified", "trap": ""},
                            {"rule": "old trap", "evidence": ["F3"], "status": "contradicted", "trap": "it went -20%"}]}
    b3 = Path(tempfile.mkdtemp())
    shutil.copytree(base / "logs", b3 / "logs")
    asyncio.run(ls.dream("merch", b3, ask=all_fail, now=t + 60))
    got3 = ls.load(b3).get("merch") or {}
    case("a contradicted lesson skips the check; when the rest fail, t still moves on",
         [x["rule"] for x in got3.get("lessons") or []] == ["old trap"] and got3.get("t") == t + 60, got3)

    async def str_idx(prompt, **k):
        if prompt.startswith("Check each lesson"):
            return {"pass": ["0"]}
        return {"lessons": [{"rule": "ok", "evidence": ["F3"], "status": "unverified", "trap": ""}]}
    b4 = Path(tempfile.mkdtemp())
    shutil.copytree(base / "logs", b4 / "logs")
    asyncio.run(ls.dream("merch", b4, ask=str_idx, now=t + 70))
    case("string indexes from the checker count", [x["rule"] for x in (ls.load(b4).get("merch") or {}).get("lessons") or []] == ["ok"])

    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    return all(RESULTS)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
