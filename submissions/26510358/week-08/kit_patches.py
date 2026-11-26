"""Fixes applied on top of the official kit (scripts/market_kit.py, read-only) by market_runtime.py.

The service imports the official v4 kit, not the v3 copy in this folder, so fixes made to week-08/market_kit.py
(ea0c80b, 14a0954) never ran. 24 h to 2026-11-10 10:30 KST the shop was unreachable 275 min (19%):
the v4 watchdog set the public URL to a new tunnel, the market refused the join ("cannot read an agent card",
TimeoutError: a fresh quick tunnel is not yet reachable from the market), and the new tunnel then passed the
liveness check, so the join was never retried. The market kept listing the dead URL for 30-100 min.
"""
import os
import signal

JOIN_TRIES = 6           # joins with the same new tunnel before giving it up
JOIN_GAP = 20            # seconds between them: a fresh quick tunnel takes a while to be reachable from outside


def stop(kit):
    """kit.stop that also ends cloudflared: it is opened without a session of its own, so killpg on its pid finds no
    group and every replaced tunnel kept running (15 cloudflared under the kit on 2026-11-10)."""
    def _signal(proc, sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            proc.send_signal(sig)

    def stop(proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            _signal(proc, signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            try:
                _signal(proc, signal.SIGKILL)
                proc.wait(timeout=5)
            except Exception:
                pass
    return stop


def join_new_tunnel(kit, market, a, keys, url, tries=JOIN_TRIES, gap=JOIN_GAP):
    """Join with url, retrying a refusal while the tunnel still answers. Returns the join answer or None."""
    for i in range(tries):
        try:
            return market.join("shop", a.name, keys["shop"], url)
        except (Exception, SystemExit) as e:
            kit.log(f"[runtime] join with {url} refused ({i + 1}/{tries}): {str(e)[:160]}")
        if i + 1 < tries:
            kit.time.sleep(gap)
            if not kit.card_ok(url):
                kit.log(f"[runtime] {url} stopped answering while joining")
                return None
    return None


def watchdog(kit):
    def watchdog(a, agent, d, market, keys, procs, proxy, sport, pport, state, every=60):
        """kit.watchdog, except the public URL moves only after the market accepts the join, and a refused join is
        retried with the same tunnel instead of being dropped."""
        misses = 0
        while True:
            kit.time.sleep(every)
            try:
                if procs[0].poll() is not None:
                    kit.log(f"[kit] your seller exited (code {procs[0].returncode}), starting it again")
                    procs[0] = kit.start(kit.fill(agent["seller"]["cmd"], port=sport), d)
                    kit.pump(procs[0], "[seller]")
                    kit.wait_card(f"http://127.0.0.1:{sport}")
                if not a.tunnel:
                    continue
                ok = procs[1].poll() is None and kit.card_ok(proxy.public)
                misses = 0 if ok else misses + 1
                if misses < 3:
                    continue
                kit.log(f"[tunnel] {proxy.public} stopped answering, opening a new tunnel")
                kit.stop(procs[1])
                procs[1], url = kit.open_tunnel(pport)
                if not kit.wait_card(url, 120):
                    raise RuntimeError(f"the new tunnel {url} did not answer within 120 s")
                j = join_new_tunnel(kit, market, a, keys, url)
                if j is None:                   # keep the URL the market has; misses stays >= 3: rotate next loop
                    continue
                proxy.public = state["own_shop"] = url
                kit.log(f"[market] shop registered again as {j['name']} at {url}")
                misses = 0
            except (Exception, SystemExit) as e:
                kit.log(f"[kit] watchdog: {type(e).__name__}: {e}")
    return watchdog


def apply(kit):
    kit.stop = stop(kit)
    kit.watchdog = watchdog(kit)
    kit.log("[runtime] kit patches: watchdog joins before moving the URL and retries a refused join; "
            "replaced tunnels are stopped")
