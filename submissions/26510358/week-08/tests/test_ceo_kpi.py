"""2026-11-09: kpi.jsonl still logged cash 'profit' and 'surplus' percentiles after patches 3.3-3.5, so buyer saved
fell from the 0.81 to the 0.68 percentile in 24 h (13th -> 16th) while the log showed the buyer part flat."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config, market
from r3lib.org import departments


def board():
    shops = [{"name": "myeonggi", "deals": 10, "book": 200, "profit": 50, "revenue": 30, "stars": 4.5, "stars_rank": 4.3, "all": 0.6},
             {"name": "a", "deals": 10, "book": 100, "profit": 90, "revenue": 40, "stars": 4.0, "stars_rank": 4.4, "all": 0.5},
             {"name": "b", "deals": 10, "book": 300, "profit": 10, "revenue": 10, "stars": 4.8, "stars_rank": 4.2, "all": 0.7}]
    buyers = [{"name": "myeonggi", "deals": 10, "saved": 0.28, "surplus": 900, "forum": 0.87, "posts": 5, "all": 0.7},
              {"name": "a", "deals": 20, "saved": 0.30, "surplus": 100, "forum": 0.92, "posts": 9, "all": 0.8},
              {"name": "b", "deals": 5, "saved": 0.20, "surplus": 50, "forum": 0.90, "posts": 1, "all": 0.4}]
    return {"shops": shops, "buyers": buyers, "players": []}


def test_kpi_logs_the_scored_fields_and_weighted_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "get_json", lambda path, **kw: board())
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    snap = departments.ceo_kpi(None, {}, lambda *a: {})
    assert set(snap["shop"]) >= {"book", "revenue", "stars_rank", "parts"} and "profit" not in snap["shop"]
    assert set(snap["buyer"]) >= {"saved", "forum", "deals", "posts", "parts"} and "surplus" not in snap["buyer"]
    assert snap["shop"]["book"] == {"value": 200, "pct": 0.333}
    assert snap["buyer"]["saved"] == {"value": 0.28, "pct": 0.333}
    assert snap["shop"]["stars_rank"] == {"value": 4.3, "pct": 0.333}           # the per-account mean that scores
    p = snap["buyer"]["parts"]
    assert p["forum"] == round(0.3 * 0.87, 4)                      # patch 3.5: the rating itself, not a percentile
    assert p["saved"] == round(0.5 * 0.333, 4)
    assert json.loads((tmp_path / "kpi.jsonl").read_text())["buyer"]["saved"]["value"] == 0.28
