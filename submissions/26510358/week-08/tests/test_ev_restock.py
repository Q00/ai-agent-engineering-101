"""Expected-value restock: Poisson sell chance, hold loss and the per-category cap."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config, pricing
from test_r3_core import CAT, mem, settle


def test_poisson_tail():
    assert pricing.poisson_tail(2.0, 0) == 1.0
    assert abs(pricing.poisson_tail(2.0, 1) - (1 - 2.718281828 ** -2)) < 1e-6
    assert pricing.poisson_tail(2.0, 3) < pricing.poisson_tail(2.0, 2)


def test_ev_quantity_stops_when_the_next_unit_loses():
    qty, evs = pricing.ev_quantity(0, mu=2.0, price=80, cost=60, loss=10)
    assert qty >= 1 and all(e > 0 for e in evs) and evs == sorted(evs, reverse=True)
    assert pricing.ev_quantity(5, mu=0.5, price=80, cost=60, loss=10)[0] == 0          # already holding plenty
    assert pricing.hold_loss("food", 100, shelf_hours=10) == 100                       # expires first: all lost


def test_category_cap_blocks_concentration(tmp_path, monkeypatch):
    import restock_r3
    m = mem(tmp_path, monkeypatch)
    now = time.time()
    m._ingest([settle(i, "G1", 85000, now - i * 120) for i in range(1, 40)])          # chairs sell fast
    demand = [{"category": CAT["G1"]["category"], "item": "의자", "kind": "goods", "skus": 1, "median_price": 100000}]
    chairs = [{"unit": f"u{i}", "category": CAT["G1"]["category"], "cost": 60000, "sku": "G1"} for i in range(13)]
    lines, why, _ = restock_r3.plan({"balance": 5_000_000, "units": []}, demand, m, now=now)
    assert lines and lines[0]["sku"] == "G1"
    lines, why, _ = restock_r3.plan({"balance": 5_000_000, "units": chairs}, demand, m, now=now)
    assert lines == [] and "cap" in why[-1]["skip"]                                    # 13 x 60k = 780k > 25% of 3M
