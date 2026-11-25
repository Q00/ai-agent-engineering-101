"""Cooperative seller drain; not durable task migration or a zero-loss guarantee.

A restart loses what lives in this process only: the talks under way (Seller.open and the SDK's tasks), the units promised
to them and the deals waiting for their payment (StockBook). So first drain:

    echo <seller pid> > .market/seller.drain     # seller_restart.sh does this, then waits, then TERMs

Only the process with that pid drains, so the seller the kit starts again ignores the same file. While draining a new talk
is refused, a talk under way goes on, and every TICK s .market/seller_drain_status.json holds the census; "drained" is
true when no turn is being answered, no unit is promised and no deal waits for its payment (memory writes flushed).
A talk whose hold ran out (HOLD_SECONDS silent) has let its unit go by the shop's own rule: it is "stale", not waited for.
"late" deals and stale SDK talks are counted; "quiet" requires both absent. A deadline may therefore abort a future restart.
When inactive the same loop writes a memory_only census without refreshing inventory, settling payments, releasing expired
holds or flushing. Its timestamp is the resident-state observation time, not the inventory/payment as-of time. Counts include
expired but unreleased holds; flushed is always false and this observation cannot authorize a quiet restart.
"""
import asyncio
import contextlib
import json
import os
import time
from pathlib import Path

TICK = 5


class Drain:
    def __init__(self, root=None, pid=None):
        self.root = Path(root) if root is not None else Path(os.environ.get("MARKET_DIR", ".market"))
        self.pid = os.getpid() if pid is None else pid
        self.inflight = 0
        self.closed = False

    def active(self):
        try:
            return int((self.root / "seller.drain").read_text().strip()) == self.pid
        except (OSError, ValueError):
            return False

    @contextlib.contextmanager
    def turn(self):
        self.inflight += 1
        try:
            yield
        finally:
            self.inflight -= 1

    def census(self, shop, now=None, *, refresh=True):
        now = time.time() if now is None else now
        stock = shop.stock
        if refresh:
            stock.units()                   # active drain only: settles payments and lets run-out holds go
        live = sum(1 for task, deal in list(shop.open.items())
                   if not deal.get("unit") or (deal["unit"] in stock.held and stock.owner.get(deal["unit"]) == task))
        c = {"pid": self.pid, "at": now, "inflight": self.inflight, "open": live, "stale": len(shop.open) - live,
             "held": len(stock.held), "agreed": len(stock.agreed), "late": len(stock.late)}
        c["drained"] = not (c["inflight"] or c["open"] or c["held"] or c["agreed"])
        c["quiet"] = c["drained"] and not (c["late"] or c["stale"])
        return c

    async def tick(self, shop, flush):
        if not self.active():
            self.closed = False
            # Observe resident state only: do not settle, load inventory, expire holds or flush.
            # Expired-but-not-released holds remain held/open, unlike the active refreshed census.
            with shop.stock._lock:
                c = self.census(shop, refresh=False)
            c.update(census_mode="memory_only", flushed=False)
            self._write(c)
            return None
        c = self.census(shop)
        c["flushed"] = False
        if c["quiet"]:
            self.closed = True  # final latch before flush; nonquiet transitions wait for next tick
            await asyncio.to_thread(flush)
            c = self.census(shop)
            c["flushed"] = c["quiet"] and self.closed
        self._write(c)
        return c

    def _write(self, c):
        tmp = self.root / f".seller_drain_status.{self.pid}.tmp"
        tmp.write_text(json.dumps(c))
        os.replace(tmp, self.root / "seller_drain_status.json")

    async def loop(self, shop, flush, log):
        was = False
        while True:
            try:
                c = await self.tick(shop, flush)
                if (c is not None) != was:
                    was = c is not None
                    log("DRAIN on: new talks refused, talks under way go on" if was else "DRAIN off: new talks welcome")
            except Exception as e:      # noqa: BLE001 - the drain must never stop the shop
                log(f"drain check failed: {type(e).__name__}: {e}")
            await asyncio.sleep(TICK)
