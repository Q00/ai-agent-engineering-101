"""What the user must see (HARNESS.md 2.2 bus, 5 alerts, D24 draft): alerts and whether the inner runtime is alive.

Each outer pass calls check(): new alerts at warn or urgent go to logs/strategy/URGENT.md (newest first, the last
MAX_LINES), and an inner status older than INNER_STALE seconds raises one urgent alert ("the inner runtime looks
down"). The seller writes bus/status/inner.json every minute while it runs (seller.py), so a stale file means
the kit or the seller stopped.
"""
import time

from core import atomic, bus, paths

INNER_STALE = 600
MAX_LINES = 50


def urgent_path():
    return paths.root() / "logs" / "strategy" / "URGENT.md"


def check(state, now=None):
    now = now or time.time()
    inner = bus.read_status("inner")
    if state.get("inner_seen") and bus.is_stale(inner, INNER_STALE, now):
        if not state.get("inner_down_alerted"):
            bus.alert("watch", "urgent", "inner_down",
                      f"the inner runtime has not written its status for {INNER_STALE // 60} min (kit or seller stopped?)",
                      {"last": inner and inner.get("ts")})
            state["inner_down_alerted"] = True
    elif inner is not None:
        state["inner_seen"] = True
        state["inner_down_alerted"] = False
    new = [a for a in bus.alerts(state.get("alerts_since", 0)) if a["level"] in ("warn", "urgent")]
    if new:
        state["alerts_since"] = new[-1]["ts"]
        p = urgent_path()
        old = p.read_text(encoding="utf-8").splitlines()[2:] if p.exists() else []
        lines = [f"- {time.strftime('%m-%d %H:%M', time.localtime(a['ts']))} **{a['level']}** {a['writer']}: "
                 f"{a['message']}" for a in reversed(new)]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Alerts (newest first)\n\n" + "\n".join((lines + old)[:MAX_LINES]) + "\n", encoding="utf-8")
    return {"new_alerts": len(new), "inner_alive": inner is not None and not bus.is_stale(inner, INNER_STALE, now)}
