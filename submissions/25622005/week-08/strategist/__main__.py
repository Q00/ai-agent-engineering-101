"""python -m strategist [--once] [--mode apply|propose] [--shop PATH] [--home PATH] [--interval S]
python -m strategist health [--home PATH]      exit 0 while the heartbeat is fresh (container healthcheck)

Without --once it runs forever: the first run 60 s after start, then every interval.
Environment: SHOP_HOME, STRATEGIST_HOME, STRATEGIST_MODE, STRATEGIST_INTERVAL_S, MARKET_URL, HARNESS_CMD.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # policy, memory, model live next to strategist/

from strategist import run  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m strategist", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["health"], help="health: check the heartbeat and exit")
    ap.add_argument("--once", action="store_true", help="one run now, print a one-line JSON summary")
    ap.add_argument("--mode", choices=["apply", "propose"], help="propose never writes SKILL.md")
    ap.add_argument("--shop", help="the shop's home (strategy/SKILL.md, memory/, logs/)")
    ap.add_argument("--home", help="the strategist's own state directory")
    ap.add_argument("--interval", type=int, help="seconds between run starts")
    a = ap.parse_args(argv)
    try:
        cfg = run.config(shop=a.shop, home=a.home, mode=a.mode, interval=a.interval)
    except ValueError as e:
        ap.error(str(e))
    if a.command == "health":
        return run.health(cfg.home)
    if a.once:
        rec = run.run_once(cfg)
        print(run.summary(rec), flush=True)
        return 1 if rec.get("status") in ("crash", "locked") else 0
    return run.loop(cfg)


if __name__ == "__main__":
    sys.exit(main())
