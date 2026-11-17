"""No default warranty while our stars class rank holds (user 11-17 15:50 KST: 'drop the warranty and sell B too, as
long as the stars rank does not change'). Run every 15 min by watch_round.sh.

On: params_auto.json globals warranty_default_h = 0, so a goods ask that does not mention a warranty ships without
one (no refund when it breaks; ~half of asks are silent). Asks that mention a warranty still get warranty_hours.
11-17 06:45: grade C goods are sold too (sell_c 1; C carries no warranty and ~46% margin on price) under the
same watch: OFF also sets sell_c 0.
Off (back to 72) when our stars class rank is worse than START_RANK or our stars_rank falls more than DROP below
its value at the start. Logs to logs/warranty_watch.log. No model call.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import board_api  # noqa: E402

STATE = HERE / ".market" / "warranty_watch.json"
PA = HERE / "params_auto.json"
LOG = HERE / "logs" / "warranty_watch.log"
DROP = 0.005


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {m}\n")


def stars(b):
    studs = {(p.get("shop") or {}).get("name") for p in b["players"] if p.get("student")}
    v = {s["name"]: s.get("stars_rank") for s in b["shops"] if s["name"] in studs and s.get("stars_rank") is not None}
    me = v["yms979"]
    return me, 1 + sum(1 for x in v.values() if x > me)


def set_default(h, sell_c=None):
    pa = json.loads(PA.read_text(encoding="utf-8"))
    pa["globals"]["warranty_default_h"] = h
    if sell_c is not None:                      # 11-17 06:45 (user: 'sell grade C too, for the book')
        pa["globals"]["sell_c"] = sell_c
    PA.write_text(json.dumps(pa, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    st = json.loads(STATE.read_text()) if STATE.is_file() else {}
    me, rk = stars(board_api.fetch(timeout=20, tries=2, wait=10))
    if "start" not in st:
        st = {"start": me, "start_rank": rk, "on": True, "t": time.time()}
        set_default(0)
        log(f"ON: warranty_default_h 0 (stars_rank {me}, class rank {rk})")
    elif st["on"] and (rk > st["start_rank"] or me < st["start"] - DROP):
        st["on"] = False
        set_default(72, 0)
        log(f"OFF (and sell_c 0): stars_rank {me} (start {st['start']}), class rank {rk} (start {st['start_rank']}) -> warranty_default_h 72")
    else:
        log(f"{'on' if st['on'] else 'off'}: stars_rank {me}, class rank {rk}")
    STATE.write_text(json.dumps(st))


if __name__ == "__main__":
    main()
