"""One-off repair (11/6, the user's call "몫 사냥꾼 ①"): before 11/6 12:40 a purchase our own kit refused for our
terms ("not paying: N h of shelf life left, the policy asks for M", or "grade C, the policy accepts ['A', 'B']") was
marked unpaid against the shop. Three in a row lock a shop out for a day, and so the shops that sold us food
cheapest (olstjr at 0.60 of list, jiseokkk at 0.63, 11im at 0.61) were passed over. buyer_memory.sync has not marked
these since 12:40; this takes the old marks back out of buyer/notebook.json.

For each records/unpaid_buys.jsonl row whose reason is our policy and that is not flagged policy, the shop's
"unpaid" outcome recorded with it (sync writes both in the same moment: within MATCH seconds) is removed and its
unpaid count lowered. Running it again removes only marks that are back (a buyer process that had loaded the
notebook before the repair may save it over the repair), so run it twice a few minutes apart. The notebook is
copied to buyer/notebook.json.bak-1106 first (once); the removed marks are listed in buyer/policy_marks_removed.json.
(Dry run on the server at 16:50: 42 marks from 15 shops; olstjr 3 failures in a row -> 0, jiseokkk 3 -> 1.)

    MARKET_DIR=~/market/round3/.market python deploy/repair_policy_marks_1106.py [--dry]
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import buyer_memory as memory  # noqa: E402
import common  # noqa: E402

MATCH = 5


def ours(reason) -> bool:
    r = str(reason or "")
    return "policy asks" in r or "the policy accepts" in r


def main() -> int:
    dry = "--dry" in sys.argv
    folder = memory.folder()
    bak = folder / "notebook.json.bak-1106"
    if not dry and not bak.exists() and (folder / "notebook.json").exists():
        shutil.copy2(folder / "notebook.json", bak)
    nb = memory.load()
    before = {s: memory.failures(nb, s) for s in nb}
    removed = []
    for u in common.records("unpaid_buys"):
        if u.get("policy") or not ours(u.get("reason")):
            continue
        e = nb.get(u.get("shop"))
        if not e:
            continue
        at = memory.times(e)
        hits = [i for i, (w, t) in enumerate(zip(e["recent"], at)) if w == "unpaid" and t is not None
                and abs(t - (u.get("ts") or 0)) <= MATCH]
        if not hits:
            continue
        i = min(hits, key=lambda j: abs(at[j] - u["ts"]))
        e["recent"] = e["recent"][:i] + e["recent"][i + 1:]
        e["recent_at"] = at[:i] + at[i + 1:]
        e["unpaid"] = max(0, (e.get("unpaid") or 0) - 1)
        removed.append({"shop": u["shop"], "ts": int(u["ts"]), "reason": str(u.get("reason"))[:80]})
    if not dry:
        memory.save(nb)
        common.write_json(folder / "policy_marks_removed.json",
                          (common.read_json(folder / "policy_marks_removed.json", []) or []) + removed)
    changed = sorted({r["shop"] for r in removed})
    print(f"{'would remove' if dry else 'removed'} {len(removed)} policy marks from {len(changed)} shops")
    for s in changed:
        print(f"  {s}: failures in a row {before.get(s)} -> {memory.failures(nb, s)}, recent now {nb[s]['recent'][-6:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
