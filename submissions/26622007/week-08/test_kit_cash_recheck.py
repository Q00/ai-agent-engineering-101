"""Kit restock: the balance is read again right before every supply POST and before every retry after a 429, and a
line that would take it under the cash floor the restock command printed is not sent (GPT Pro 14, 대표님 11/10 23:48).
The command checks the cash once, up to ~100 s before its last line goes out; refunds and other buys move the balance
meanwhile."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))
import market_kit as kit           # noqa: E402


class Market:
    """A shop's balance, a script of answers to POST /api/supply (a status or "ok"), and what was asked."""

    def __init__(self, balance, script=(), cost=100000, inventory_status=200, took=0):
        self.balance, self.script, self.cost, self.inventory_status = balance, list(script), cost, inventory_status
        self.calls, self.token = [], {"shop": "t"}
        self.took, self.clock, self.supplies = took, [0], []     # seconds a call takes on run()'s clock; (sent, answered)

    def call(self, method, path, role="buyer", **kw):
        self.calls.append((method, path, (kw.get("json") or {}).get("sku")))
        sent = self.clock[0]
        self.clock[0] += self.took
        if path == "/api/supply":
            self.supplies.append((sent, self.clock[0]))
        if path == "/api/inventory":
            return httpx.Response(self.inventory_status, json={"round": 3, "balance": self.balance, "units": []})
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, httpx.Response):
            return step
        if step == "ok":
            self.balance -= self.cost * kw["json"]["qty"]
            return httpx.Response(200, json={"total": self.cost * kw["json"]["qty"], "balance": self.balance})
        if callable(step):
            step(self)
            return httpx.Response(429, json={"wait": 0})
        return httpx.Response(step, json={"wait": 0, "error": "x"})


NOW = 1800000000.0


def run(market, out, logs=None, sleep=None, restock=None, last=None):
    """One restock run on a clock that only sleep moves. sleep: a Mock that sees every wait (rounded to 0.1 s).
    restock: inventory's supply window. last: seconds since the market took the last supply call (None: never)."""
    md = Path(tempfile.mkdtemp())
    logs = [] if logs is None else logs
    clock = [NOW]

    def slept(s):
        clock[0] += s
        if sleep is not None:
            sleep(round(s, 1))
    inv = {"balance": market.balance, "units": []}
    if restock is not None:
        inv["restock"] = restock
    kit._supply_last[0] = None if last is None else NOW - last
    with mock.patch.object(kit, "run_buyer", return_value=[]), mock.patch.object(kit, "last_json", return_value=out), \
            mock.patch.object(kit, "log", side_effect=logs.append), mock.patch.object(kit.time, "sleep", side_effect=slept), \
            mock.patch.object(kit.time, "monotonic", side_effect=lambda: clock[0]), \
            mock.patch.object(kit.time, "time", side_effect=lambda: clock[0]):
        market.clock = clock
        try:
            kit.restock({"restock": {"cmd": "x"}}, md, market, md, inv, [])
        finally:
            kit._supply_last[0] = None
    posts = [c[2] for c in market.calls if c[1] == "/api/supply"]
    return posts, logs


def waits(sleep):
    return [c.args[0] for c in sleep.call_args_list]


PACE = [9.2, 1.0]       # between two supply calls: the wait up to the balance read, then what is left of the 10.2 s


CASH = {"floor": 1000000, "unit": {"G1": 150000, "G2": 150000, "G3": 150000}}
BUY = [{"sku": "G1", "qty": 2}, {"sku": "G2", "qty": 2}, {"sku": "G3", "qty": 2}]


class KitCashRecheckTest(unittest.TestCase):
    def test_a_command_sends_up_to_twenty_lines(self):
        buy = [{"sku": f"G{i}", "qty": 1} for i in range(25)]
        self.assertEqual(run(Market(10000000), {"buy": buy[:18]})[0], [b["sku"] for b in buy[:18]])
        self.assertEqual(run(Market(10000000), {"buy": buy})[0], [b["sku"] for b in buy[:20]])

    def test_closed_window_stops_later_lines_without_sleeping(self):
        closed = httpx.Response(429, json={"error": "supply is closed", "wait": 1054.0,
                                           "opens_at": 12345, "closes_at": 12525})
        m = Market(10000000, script=["ok", "ok", closed])
        sleep = mock.Mock()
        posts, logs = run(m, {"buy": [{"sku": f"G{i}", "qty": 1} for i in range(18)]}, sleep=sleep)
        self.assertEqual(posts, ["G0", "G1", "G2"])
        self.assertEqual(waits(sleep), PACE * 2)           # the pace between calls; nothing after the closed answer
        self.assertIn("[supply] window closed: 16 line(s) not sent", logs)

    def test_short_wait_still_retries_and_rechecks_cash(self):
        m = Market(4000000, script=[httpx.Response(429, json={"wait": 7}), "ok"])
        sleep = mock.Mock()
        cash = {"floor": 1000000, "unit": {"G0": 100000}}
        posts, _ = run(m, {"buy": [{"sku": "G0", "qty": 1}], "cash": cash}, sleep=sleep)
        self.assertEqual(posts, ["G0", "G0"])
        sleep.assert_called_once_with(7.2)
        self.assertEqual([c[1] for c in m.calls[:4]],
                         ["/api/inventory", "/api/supply", "/api/inventory", "/api/supply"])

    def test_a_short_wait_that_names_the_window_is_still_retried(self):
        m = Market(10000000, script=[httpx.Response(429, json={"wait": 7, "opens_at": 12345, "closes_at": 12525}), "ok"])
        sleep = mock.Mock()
        posts, logs = run(m, {"buy": [{"sku": "G0", "qty": 1}, {"sku": "G1", "qty": 1}]}, sleep=sleep)
        self.assertEqual(posts, ["G0", "G0", "G1"])
        self.assertEqual(waits(sleep), [7.2] + PACE)
        self.assertFalse(any("window closed" in line for line in logs))
        self.assertIn("[supply] 429 for G0 (try 1 of 3): wait 7.0 s", logs)

    def test_a_window_that_opens_within_a_minute_is_waited_for(self):
        # restock.py sends lines up to 50 s before a window opens (opening.WINDOW_LEAD): three tries, 30 s apart
        early = [httpx.Response(429, json={"error": "supply is closed", "wait": w, "opens_at": 1, "closes_at": 181})
                 for w in (45, 15)]
        sleep = mock.Mock()
        posts, logs = run(Market(10000000, script=early + ["ok"]),
                          {"buy": [{"sku": "G0", "qty": 1}, {"sku": "G1", "qty": 1}]}, sleep=sleep)
        self.assertEqual(posts, ["G0", "G0", "G0", "G1"])
        self.assertEqual(waits(sleep), [30.2, 15.2] + PACE)
        self.assertFalse(any("window closed" in line for line in logs))
        self.assertEqual([line for line in logs if " 429 " in line],
                         ["[supply] 429 for G0 (try 1 of 3): wait 45.0 s", "[supply] 429 for G0 (try 2 of 3): wait 15.0 s"])

    def test_a_run_that_starts_before_the_window_sends_its_first_line_when_it_opens(self):
        # independent review 5e70b9e C1: a run 31-50 s before the window must not lose its lines
        buy = [{"sku": f"G{i}", "qty": 1} for i in range(3)]
        for ahead in (50, 45, 31, 5):
            sleep, m = mock.Mock(), Market(10000000)
            posts, logs = run(m, {"buy": buy}, sleep=sleep, restock={"open": False, "opens_at": NOW + ahead})
            self.assertEqual(posts, ["G0", "G1", "G2"], ahead)
            self.assertEqual(waits(sleep), [ahead + 0.2] + PACE * 2, ahead)       # no call before it opens: no 429
            self.assertIn(f"[supply] window opens in {ahead} s: 3 line(s) wait for it", logs)
            self.assertFalse(any(" 429 " in line or "window closed" in line for line in logs))
        # the market's clock is behind ours: it still says closed for 3 s, then 45 s -> 15 s as in the review's case
        for script, want in (([httpx.Response(429, json={"wait": 3})], [45.2, 3.2] + PACE * 2),
                             ([httpx.Response(429, json={"wait": w}) for w in (45, 15)], [45.2, 30.2, 15.2] + PACE * 2)):
            sleep = mock.Mock()
            posts, logs = run(Market(10000000, script=script + ["ok"]), {"buy": buy}, sleep=sleep,
                              restock={"open": False, "opens_at": NOW + 45})
            self.assertEqual(posts, ["G0"] * len(script) + ["G0", "G1", "G2"])
            self.assertEqual(waits(sleep), want)

    def test_only_a_window_that_opens_within_a_minute_is_waited_for_by_the_clock(self):
        buy = [{"sku": "G0", "qty": 1}]
        for restock in ({"open": True, "closes_at": NOW + 100}, {"open": False, "opens_at": NOW + 61},
                        {"open": False, "opens_at": NOW - 5}, {"open": False, "opens_at": None},
                        {"open": False, "opens_at": "x"}, {"open": False}, "x", None):
            sleep = mock.Mock()
            self.assertEqual(run(Market(10000000), {"buy": buy}, sleep=sleep, restock=restock)[0], ["G0"], restock)
            sleep.assert_not_called()
        sleep = mock.Mock()                                 # nothing to send: no wait
        self.assertEqual(run(Market(10000000), {"buy": []}, sleep=sleep, restock={"open": False, "opens_at": NOW + 30})[0], [])
        sleep.assert_not_called()
        closed = httpx.Response(429, json={"wait": 600})   # far from a window the market's answer ends the run as before
        posts, logs = run(Market(10000000, script=[closed]), {"buy": buy}, restock={"open": False, "opens_at": NOW + 600})
        self.assertEqual(posts, ["G0"])
        self.assertIn("[supply] window closed: 1 line(s) not sent", logs)

    def test_the_kit_waits_out_the_markets_ten_seconds_itself(self):
        buy = [{"sku": f"G{i}", "qty": 1} for i in range(4)]
        sleep, m = mock.Mock(), Market(10000000)
        posts, logs = run(m, {"buy": buy, "cash": {"floor": 1000000, "unit": {b["sku"]: 100000 for b in buy}}}, sleep=sleep)
        self.assertEqual(posts, ["G0", "G1", "G2", "G3"])
        self.assertEqual(waits(sleep), PACE * 3)            # one call per line, 10.2 s after the one before
        self.assertFalse(any(" 429 " in line for line in logs))
        paths = [c[1] for c in m.calls]                     # the balance is read after the wait, right before the POST
        self.assertEqual(paths[:5], ["/api/inventory", "/api/supply", "/api/inventory", "/api/inventory", "/api/supply"])
        sleep = mock.Mock()                                 # the run before this one ended 4 s ago: 6.2 s are left
        self.assertEqual(run(Market(10000000), {"buy": buy[:1]}, sleep=sleep, last=4)[0], ["G0"])
        self.assertEqual(waits(sleep), [5.2, 1.0])
        sleep = mock.Mock()
        self.assertEqual(run(Market(10000000), {"buy": buy[:1]}, sleep=sleep, last=11)[0], ["G0"])
        sleep.assert_not_called()
        sleep = mock.Mock()                                 # a refusal that is not a 429 counts as a call; a held line does not
        posts, _ = run(Market(10000000, script=[400]), {"buy": buy[:2]}, sleep=sleep)
        self.assertEqual((posts, waits(sleep)), (["G0", "G1"], PACE))
        sleep = mock.Mock()
        cash = {"floor": 1000000, "unit": {"G0": 99999999, "G1": 100000}}
        posts, _ = run(Market(10000000), {"buy": buy[:2], "cash": cash}, sleep=sleep)
        self.assertEqual(posts, ["G1"])
        sleep.assert_not_called()

    def test_the_balance_read_takes_part_of_the_wait_not_more_time(self):
        # independent review a6452e3 I1: wait 10.2 s, then read, then POST made a line 10.2 s + two round trips
        buy = [{"sku": f"G{i}", "qty": 1} for i in range(18)]
        cash = {"floor": 1000000, "unit": {b["sku"]: 100000 for b in buy}}
        for took in (0.3, 0.9):
            m = Market(10000000, took=took)
            posts, logs = run(m, {"buy": buy, "cash": cash})
            self.assertEqual(len(posts), 18)
            gaps = [round(b[0] - a[1], 3) for a, b in zip(m.supplies, m.supplies[1:])]
            self.assertEqual(set(gaps), {10.2}, took)       # answered -> next sent: the market's 10 s and the margin, no more
            reads = [i for i, c in enumerate(m.calls) if c[1] == "/api/inventory"]
            self.assertEqual(len(reads), 18 + 18)           # before every POST, and the refresh after it
        m = Market(10000000, took=1.5)                       # a read slower than the second kept for it adds only what it runs over
        run(m, {"buy": buy[:3], "cash": cash})
        self.assertEqual([round(b[0] - a[1], 3) for a, b in zip(m.supplies, m.supplies[1:])], [10.7, 10.7])
        m = Market(10000000, took=0.3)                       # 0.3 s a call: a line every 10.5 s, 18 lines inside 180 s
        run(m, {"buy": buy, "cash": cash}, restock={"open": False, "opens_at": NOW + 30})
        self.assertLess(m.supplies[-1][1] - (NOW + 30), 180)

    def test_a_restock_run_starts_forty_seconds_before_a_window_opens(self):
        import opening
        self.assertLess(kit.SUPPLY_AHEAD, opening.WINDOW_LEAD)                  # restock.py sends lines on that run
        self.assertLessEqual(opening.WINDOW_LEAD, 60)                           # and the kit waits for the window
        closed = {"restock": {"open": False, "opens_at": NOW + 35}}
        self.assertTrue(kit.restock_due({}, NOW - 61, NOW))                     # the usual minute
        self.assertFalse(kit.restock_due({}, NOW - 59, NOW))
        self.assertTrue(kit.restock_due(closed, NOW - 20, NOW))                 # 13:00:18's case: the last run was too early
        self.assertFalse(kit.restock_due(closed, NOW - 4, NOW))                 # one started 39 s before the window: enough
        self.assertFalse(kit.restock_due({"restock": {"open": False, "opens_at": NOW + 41}}, NOW - 20, NOW))
        self.assertTrue(kit.restock_due({"restock": {"open": False, "opens_at": NOW + 40}}, NOW - 20, NOW))
        self.assertFalse(kit.restock_due({"restock": {"open": False, "opens_at": NOW}}, NOW - 20, NOW))
        for r in ({"open": True, "closes_at": NOW + 35}, {"open": False}, {"open": False, "opens_at": "x"}, "x", None):
            self.assertFalse(kit.restock_due({"restock": r}, NOW - 20, NOW), r)

    def test_long_wait_without_open_time_stops_and_bad_429_bodies_retry(self):
        buy = [{"sku": "G0", "qty": 1}, {"sku": "G1", "qty": 1}]
        sleep = mock.Mock()
        posts, logs = run(Market(10000000, script=[httpx.Response(429, json={"wait": 61})]),
                          {"buy": buy}, sleep=sleep)
        self.assertEqual(posts, ["G0"])
        self.assertIn("[supply] window closed: 2 line(s) not sent", logs)
        sleep.assert_not_called()
        for answer in (httpx.Response(429, text="not json"), httpx.Response(429, json=[]),
                       httpx.Response(429, json={}), httpx.Response(429, json={"wait": "bad"})):
            sleep.reset_mock()
            posts, _ = run(Market(10000000, script=[answer, "ok"]), {"buy": buy[:1]}, sleep=sleep)
            self.assertEqual(posts, ["G0", "G0"])
            sleep.assert_called_once_with(10.2)

    def test_cash_floor_holds_a_later_line_among_eighteen(self):
        buy = [{"sku": f"G{i}", "qty": 1} for i in range(18)]
        cash = {"floor": 1000000, "unit": {b["sku"]: 100000 for b in buy}}
        m = Market(2700000)
        posts, logs = run(m, {"buy": buy, "cash": cash})
        self.assertEqual(posts, [b["sku"] for b in buy[:17]])
        self.assertEqual(m.balance, 1000000)
        self.assertTrue(any("G17" in line and "held" in line for line in logs))

    def test_a_command_without_a_cash_floor_is_sent_as_before(self):
        m = Market(10)                                       # no "cash" in the answer: the kit asks nothing more
        posts, logs = run(m, {"buy": BUY})
        self.assertEqual(posts, ["G1", "G2", "G3"])
        self.assertEqual([c[1] for c in m.calls].count("/api/inventory"), 3)       # the refresh after each supply only

    def test_every_line_is_checked_against_the_balance_of_that_moment(self):
        m = Market(1650000, cost=150000)                    # 650k over the floor: two lines of 300k fit, the third does not
        posts, logs = run(m, {"buy": BUY, "cash": CASH})
        self.assertEqual(posts, ["G1", "G2"])
        self.assertEqual(m.balance, 1050000)
        self.assertTrue(any("held" in x and "G3" in x and "cash floor" in x for x in logs), logs)
        self.assertEqual(m.calls[0][:2], ("GET", "/api/inventory"))               # read before the first POST

    def test_money_that_left_since_the_command_ran_stops_the_first_line(self):
        m = Market(1200000)                                  # a refund was paid after the command checked 1.65M
        posts, logs = run(m, {"buy": BUY, "cash": CASH})
        self.assertEqual(posts, [])
        self.assertEqual(sum("held" in x for x in logs), 3)

    def test_a_cheaper_line_after_a_held_one_still_goes(self):
        m = Market(1200000, cost=40000)
        cash = {"floor": 1000000, "unit": {"G1": 150000, "G2": 50000, "G3": 150000}}
        posts, _ = run(m, {"buy": [{"sku": "G1", "qty": 2}, {"sku": "G2", "qty": 2}, {"sku": "G3", "qty": 1}], "cash": cash})
        self.assertEqual(posts, ["G2"])                     # 100k fits in the 200k over the floor; then 120k is left: G3 no

    def test_the_balance_is_read_again_before_a_retry_after_429(self):
        def refund(market):                                  # while the kit waits out the 429 a refund takes 500k
            market.balance -= 500000
        m = Market(1650000, script=[refund])
        posts, logs = run(m, {"buy": BUY[:1], "cash": CASH})
        self.assertEqual(posts, ["G1"])                     # the first try; no second: 1.15M - 300k is under the floor
        self.assertTrue(any("held" in x for x in logs))
        m = Market(1650000, script=[429, "ok"])
        posts, _ = run(m, {"buy": BUY[:1], "cash": CASH})
        self.assertEqual(posts, ["G1", "G1"])               # nothing changed: the retry goes
        gets = [i for i, c in enumerate(m.calls) if c[1] == "/api/inventory"]
        self.assertEqual(gets[:2], [0, 2])                  # before the POST and again before the retry

    def test_a_balance_that_cannot_be_read_or_a_line_without_a_cost_is_not_sent(self):
        m = Market(9000000, inventory_status=503)
        posts, logs = run(m, {"buy": BUY, "cash": CASH})
        self.assertEqual(posts, [])
        m = Market(9000000)
        posts, logs = run(m, {"buy": BUY, "cash": {"floor": 1000000, "unit": {"G1": 150000, "G2": "x", "G3": -1}}})
        self.assertEqual(posts, ["G1"])
        for bad in ({"floor": "x", "unit": CASH["unit"]}, {"floor": float("nan"), "unit": CASH["unit"]},
                    {"floor": True, "unit": CASH["unit"]}, {"floor": 1000000, "unit": None}, {"floor": -5, "unit": CASH["unit"]}):
            self.assertEqual(run(Market(9000000), {"buy": BUY, "cash": bad})[0], [], bad)   # a broken floor buys nothing
        self.assertEqual(run(Market(9000000), {"buy": BUY, "cash": "x"})[0], ["G1", "G2", "G3"])   # not an object: no floor given

    def test_the_restock_command_prints_its_floor_and_unit_costs(self):
        import restock
        catalog = {"G1": {"kind": "goods", "list_price": 200000}, "F1": {"kind": "food", "list_price": 20000}}
        inv = {"surge": [{"sku": "F1", "factor": 1.1}]}
        cash = restock.cash_note([{"sku": "G1", "qty": 2}, {"sku": "F1", "qty": 20}], inv, catalog, 2500000)
        self.assertEqual(cash["floor"], 2500000)
        self.assertEqual(cash["unit"]["G1"], restock.worst("G1", 2, catalog, {}) * (1 + restock.hard_limits.BUY_STORAGE))
        self.assertEqual(cash["unit"]["G1"], 151500.0)                              # 0.75 x list and a day of storage
        self.assertGreater(cash["unit"]["F1"], 20000 * 0.75 * 1.1)                  # the surge factor its last unit pays
        self.assertIsNone(restock.cash_note([{"sku": "G1", "qty": 1}], inv, catalog, float("inf")))   # no usable floor
        self.assertIsNone(restock.cash_note([], inv, catalog, 2500000))
        twice = restock.cash_note([{"sku": "F1", "qty": 20}, {"sku": "F1", "qty": 1}], inv, catalog, 2500000)
        self.assertEqual(twice["unit"]["F1"], cash["unit"]["F1"])                   # a SKU on two lines: the dearer unit

    def test_a_line_that_leaves_exactly_the_floor_goes_and_a_refusal_reads_the_balance_again(self):
        m = Market(1300000, cost=150000)
        posts, _ = run(m, {"buy": BUY[:1], "cash": CASH})                           # 1.3M - 300k = the floor: not under it
        self.assertEqual(posts, ["G1"])
        m = Market(1299999, cost=150000)
        self.assertEqual(run(m, {"buy": BUY[:1], "cash": CASH})[0], [])
        m = Market(5000000, script=[500])                   # a refusal that is not a 429: the next line reads the balance
        posts, logs = run(m, {"buy": BUY[:2], "cash": CASH})
        self.assertEqual(posts, ["G1", "G2"])
        self.assertEqual([c[1] for c in m.calls[:4]], ["/api/inventory", "/api/supply", "/api/inventory", "/api/supply"])
        self.assertEqual(run(Market(float("inf")), {"buy": BUY[:1], "cash": CASH})[0], [])       # not a balance


if __name__ == "__main__":
    unittest.main()
