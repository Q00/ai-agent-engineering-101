"""Deploy before/after numbers come from kpi.jsonl; the baseline file keeps what the week-11 re-measure needs."""
import json

from r3lib import measure


def row(ts, book, all_=0.7, rank=18):
    return {"ts": ts, "shop": {"all": all_, "book": {"value": book}}, "buyer": {"all": 0.75}, "player": {"rank": rank}}


def test_compare_means_each_side_and_book_per_hour():
    rows = [row(0, 100e6), row(1800, 101e6), row(3600, 102e6, 0.72, 17), row(5400, 105e6, 0.72, 17), row(7200, 108e6, 0.72, 17)]
    out = measure.compare(3600, hours=1, rows=rows)
    assert out["rows"] == {"before": 2, "after": 3}
    assert out["shop_all"] == {"before": 0.7, "after": 0.72, "change": 0.02}
    assert out["rank"]["change"] == -1
    assert out["book_per_h"] == {"before": 2_000_000, "after": 6_000_000}
    assert out["saved"]["change"] is None                       # field missing on both sides


def test_baseline_writes_our_values_and_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "kpi_rows", lambda path=None: [row(1, 5)])
    board = {"shops": [{"name": "myeonggi", "deals": 5, "book": 3, "revenue": 30, "stars_rank": 4.4},
                       {"name": "x", "deals": 9, "book": 5, "revenue": 50, "stars_rank": 4.0}],
             "buyers": [{"name": "myeonggi", "deals": 900, "saved": 0.3, "forum": 0.88, "posts": 7},
                        {"name": "y", "deals": 10, "saved": 0.2, "forum": 0.9, "posts": 1}],
             "players": [{"shop": {"name": "x"}, "total": 1.7}, {"shop": {"name": "myeonggi"}, "total": 1.4}]}
    snap, out = measure.baseline("t", board=board, now=1000.0, out_dir=tmp_path)
    saved = json.loads(out.read_text())
    assert saved["player"] == {"rank": 2, "of": 2, "total": 1.4} and saved["buyer"]["saved"] == 0.3
    assert 0 < saved["model"]["saved_threshold"] <= 0.3 + 1e-9 and saved["kpi"]["ts"] == 1
