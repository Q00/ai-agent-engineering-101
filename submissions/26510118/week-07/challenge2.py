"""Week 07, challenge 2 -- the shops that cheated my buyer, and how it came to avoid them.

    python challenge2.py

Reads only, writes nothing: the kit's log for the evidence, and the last shop
list the kit handed the buyer (logs/challenge-2/shops-snapshot.json, copied
from .market/shops.json at 21:43 on 10-20). It runs the buyer's shop filter on
that list twice, with the memory as it was and with Round 0's lessons folded
in, so the difference is the whole effect of import_round0.
"""
import copy
import json
from pathlib import Path

import market_buyer as mb

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "logs" / "challenge-2" / "shops-snapshot.json"


def evidence() -> None:
    """Each wrong receipt in the kit's log, with the order before it and the
    market's verdict after it."""
    lines = mb.KIT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        if not mb._WRONG.search(line):
            continue
        order = next(lines[j] for j in range(i, -1, -1) if mb._ORDER.search(lines[j]))
        shop = next(lines[j] for j in range(i, -1, -1) if mb._SHOP.search(lines[j]))
        deal = next(lines[j] for j in range(i, -1, -1) if "TASK_STATE_COMPLETED" in lines[j])
        verdict = [lines[j] for j in range(i + 1, min(i + 8, len(lines)))
                   if "[sign]" in lines[j] or "[market] ledger" in lines[j]]
        print("\n".join(["", order, shop, deal, line, *verdict]))


def without_round0(mem: dict) -> dict:
    """The memory minus what import_round0 added, so the 'before' table is the
    same however many times the buyer has run since."""
    mem = copy.deepcopy(mem)
    for name in list(mem):
        e = mem[name]
        e["trades"] = [t for t in e.get("trades", []) if t.get("src") != "round-0 kit log"]
        if e["trades"]:
            e["agg"] = mb.aggregate(e["trades"])
        else:
            del mem[name]
    return mem


def table(shops: list, mem: dict, label: str) -> None:
    print(f"\n--- {label}")
    for sh in shops:
        ws = mb.weighted_stars(sh.get("reviews"))
        past = mem.get(sh["name"])
        bad = mb.why_bad(ws, past)
        if bad or sh["name"] in ("elm-street", "maple-home"):
            print(f"  {sh['name']:<14} stars {ws if ws is None else f'{ws:.2f}':<5} "
                  f"score {mb.score(sh['name'], ws, past, mem, len(sh.get('reviews') or [])):.2f}  "
                  f"{'AVOIDED: ' + bad if bad else 'a candidate'}")


def main() -> int:
    print("# the wrong receipts week-06's buyer caught in Round 0 (from logs/market-kit.log)")
    evidence()
    shops = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    mem = without_round0(mb.load_mem(HERE / ".market" / "memory.json"))
    print(f"\n# the buyer's filter on the {len(shops)} shops of the last live order "
          f"(only avoided shops and the two cheats are listed)")
    table(shops, mem, "memory as it was: Round 0 never reached it")
    mem = copy.deepcopy(mem)
    n = mb.import_round0(mem)
    table(shops, mem, f"after import_round0 folded {n} wrong receipt(s) into memory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
