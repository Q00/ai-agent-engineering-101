"""v2.3 (2026-11-07, checkout refusals): the seller does not offer a unit it sold in the last ACCEPT_HOLD_S or a unit
the kit refused at checkout ("unit X is not in stock for this order") in the last REFUSED_HOLD_S. 24 h to 11-07
12:30 KST: 305 refusals on 25 units; 19 units were sold to another buyer seconds before, 6 units were held by an
unpaid checkout session for up to 6.3 h and drew most of the refusals."""

import inspect
import time

import pytest

from tests import test_v210_stars as T

S, P, R3 = T.S, T.P, T.R3


@pytest.fixture(autouse=True)
def _buyer_log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "buyer_attempts.jsonl"))  # never the repo's logs/


def _at(day_now, hh, mm):
    lt = time.localtime(day_now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))


def _line(t, unit):
    return time.strftime("%H:%M:%S", time.localtime(t)) + f" [acp] checkout refused: unit {unit} is not in stock for this order"


def test_refused_units_reads_the_recent_shop_side_refusals(tmp_path):
    now = _at(time.time(), 12, 30)
    log = tmp_path / "market-kit.log"
    log.write_text("\n".join([
        _line(now - 7 * 3600, "u-0001aa"),                     # older than REFUSED_HOLD_S
        _line(now - 3600, "u-0002bb"),
        "12:00:01 [acp] settled 생수 at 3753, ledger #1, unit u-0003cc grade B",
        _line(now - 1800, "u-0004dd").replace("checkout refused: unit", 'checkout refused: 409 {"error":"unit'),  # our buyer at another shop
        "garbage line",
    ]) + "\n", encoding="utf-8")
    assert S.refused_units(path=log, now=now) == {"u-0002bb"}


def test_refused_units_reads_last_night_after_midnight(tmp_path):
    now = _at(time.time(), 0, 20)
    log = tmp_path / "market-kit.log"
    log.write_text(_line(now - 30 * 60, "u-0005ee") + "\n" + _line(now - 5 * 60, "u-0006ff") + "\n", encoding="utf-8")
    assert S.refused_units(path=log, now=now) == {"u-0005ee", "u-0006ff"}
    assert S.refused_units(path=log, now=now, hold_s=600) == {"u-0006ff"}  # 23:50 is yesterday, not 23 h ahead


def test_refused_units_without_a_log_is_empty(tmp_path):
    assert S.refused_units(path=tmp_path / "none.log") == set()


def test_held_joins_open_deals_recent_accepts_and_refusals(tmp_path, monkeypatch):
    now = _at(time.time(), 12, 30)
    log = tmp_path / "market-kit.log"
    log.write_text(_line(now - 600, "u-0007aa") + "\n", encoding="utf-8")
    monkeypatch.setattr(S, "KIT_LOG", log)
    s = S.Seller(pol=P.load(R3 / "policy.json"))
    s.open = {"t1": {"unit": {"unit": "u-open"}}}
    s.accepted = {"u-sold-now": now - 100, "u-sold-old": now - S.ACCEPT_HOLD_S - 1}
    assert s.held(now) == {"u-open", "u-sold-now", "u-0007aa"}
    assert s.accepted == {"u-sold-now": now - 100}               # expired accepts are dropped


def test_find_unit_moves_to_another_unit_of_the_sku():
    units = [T.goods("u1", "A", 1), T.goods("u2", "A", 2)]
    text = "I want to buy 매트리스 (G0000a1)"
    assert T.pick(text, units)["unit"] == "u1"
    assert T.pick(text, units, taken={"u1"})["unit"] == "u2"
    assert T.pick(text, units, taken={"u1", "u2"}) is None    # no unit left: no_stock, not a refused checkout


def test_the_seller_wires_held_into_the_pick_and_records_its_accepts():
    src = inspect.getsource(S.Seller._execute)
    assert "find_unit(text, inv, self.held(now), self.pol" in src
    assert src.index("receipt = sign(receipt)") < src.index('self.accepted[u["unit"]] = time.time()') < src.index("add_artifact")
