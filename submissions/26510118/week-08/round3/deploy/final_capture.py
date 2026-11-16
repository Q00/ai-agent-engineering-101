"""Keep what Round 3's end looked like (11/16, the user's call, "둘 다 준비해").

The rank is the board's at 11/17 23:59 KST, and REPORT.md part 3 quotes the settles, the Players rank and log lines,
but the board cannot be read back later and logs/market-kit.log turns over every 20 MB (the old .1 is overwritten).
Run around the end (23:58:30, 00:00:30 and 00:05 KST), each run writes OUT/<label>/:

    board.json        GET /api/board as the market served it
    standing.txt      the class table (student_total, student_pct) and our own rows, read from that board
    scoreboard.json   our analysis's scoreboard, objective and experiments as they were
    logs/             with --logs: copies of logs/market-kit.log(.1), analysis.log, manual_runs.log,
                      seller_restarts.log (copied, never changed; the originals are left as they are)

    cd ~/market/round3 && MARKET_DIR=$PWD/.market ~/market/.venv/bin/python deploy/final_capture.py LABEL [--logs] [--out DIR]
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import analysis                                     # noqa: E402
import common                                       # noqa: E402

LOGS = ("market-kit.log", "market-kit.log.1", "analysis.log", "manual_runs.log", "seller_restarts.log")
STATE = ("scoreboard.json", "objective.json", "experiments.json", "active_rules.json", "person_rules.json")


def standing(board: dict, at: float) -> str:
    players = [p for p in board.get("players") or [] if p.get("student")]
    rows = sorted(players, key=lambda p: -(p.get("student_total") or 0))
    out = [f"board read {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(at + 9 * 3600))} KST, "
           f"{len(rows)} students (Players table, Class view: student_total, student_pct)"]
    for i, p in enumerate(rows, 1):
        mark = "  <- us" if common.is_ours(p.get("player")) else ""
        out.append(f"{i:3d}. {p.get('player', '?'):<24} student_total {p.get('student_total')}  student_pct "
                   f"{p.get('student_pct')}  total {p.get('total')}{mark}")
    for side in ("shops", "buyers"):
        mine = [r for r in board.get(side) or [] if common.is_ours(r.get("name"))]
        for r in mine:
            out.append(f"our {side[:-1]} row: " + json.dumps({k: v for k, v in r.items() if k not in ("card", "owner")},
                                                             ensure_ascii=False))
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--logs", action="store_true")
    ap.add_argument("--out", default=str(Path.home() / "market" / "final_r3"))
    a = ap.parse_args()
    at = time.time()
    dest = Path(a.out) / a.label
    dest.mkdir(parents=True, exist_ok=True)
    try:
        board = analysis.market_get("/api/board") or {}
        (dest / "board.json").write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
        (dest / "standing.txt").write_text(standing(board, at), encoding="utf-8")
    except Exception as e:                                         # noqa: BLE001  (the rest is still worth keeping)
        (dest / "board_error.txt").write_text(f"{type(e).__name__}: {e}\n", encoding="utf-8")
    state = {name: common.read_json(common.MARKET_DIR / "analysis" / name, None) for name in STATE}
    (dest / "scoreboard.json").write_text(json.dumps(dict(state, captured_at=int(at)), ensure_ascii=False), encoding="utf-8")
    if a.logs:
        (dest / "logs").mkdir(exist_ok=True)
        for name in LOGS:
            src = HERE / "logs" / name
            if src.exists():
                shutil.copy2(src, dest / "logs" / name)
    print(f"captured {a.label} into {dest}: {sorted(p.name for p in dest.iterdir())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
