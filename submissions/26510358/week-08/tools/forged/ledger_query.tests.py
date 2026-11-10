import sqlite3

import tool

NOW = 1_000_000.0


def db(path="ledger.sqlite"):
    c = sqlite3.connect(path)
    c.execute("DROP TABLE IF EXISTS settles")
    c.execute("CREATE TABLE settles (seq INTEGER, ts REAL, shop TEXT, sku TEXT, category TEXT, kind TEXT, "
              "list_price INTEGER, amount INTEGER, grade TEXT)")
    rows = [(1, NOW - 3600, "s1", "K1", "food>kimchi", "food", 10000, 6000, "A"),
            (2, NOW - 7200, "s2", "K1", "food>kimchi", "food", 10000, 5000, "B"),
            (3, NOW - 7300, "s2", "K1", "food>kimchi", "food", 10000, 4000, "C"),
            (4, NOW - 90 * 3600, "s1", "K1", "food>kimchi", "food", 10000, 9000, "A"),
            (5, NOW - 3600, "s1", "C9", "furniture>chair", "goods", 200000, 120000, "A"),
            (6, NOW - 3600, "s3", "K2", "food>kimchi", "food", 20000, 0, "A")]
    c.executemany("INSERT INTO settles VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return path


def test_a_sku_in_the_window():
    out = tool.run(db(), sku="K1", hours=24, now=NOW)
    assert out["deals"] == 3 and out["price_to_list"] == {"min": 0.4, "median": 0.5, "max": 0.6}
    assert out["refs"] == [3, 2, 1]


def test_old_deals_are_outside_and_the_window_is_capped():
    assert tool.run(db(), sku="K1", hours=1000, now=NOW)["deals"] == 3          # 90 h ago stays out: cap is 72 h
    assert tool.run(db(), sku="K1", hours=1.5, now=NOW)["deals"] == 1


def test_a_category_by_part_of_its_name_split_by_grade_and_shop():
    out = tool.run(db(), category="kimchi", by="grade", now=NOW)
    assert out["deals"] == 3 and {g["grade"]: g["deals"] for g in out["by_grade"]} == {"A": 1, "B": 1, "C": 1}
    shops = tool.run(db(), category="kimchi", by="shop", now=NOW)["by_shop"]
    assert shops[0]["shop"] == "s2" and shops[0]["deals"] == 2 and shops[0]["price_to_list"]["median"] == 0.45


def test_nothing_found_and_bad_arguments_are_answers_not_errors():
    assert tool.run(db(), sku="nope", now=NOW) == {"deals": 0}
    assert tool.run(db(), category="boats", now=NOW)["deals"] == 0
    assert "error" in tool.run(db(), now=NOW) and "error" in tool.run(db(), sku="K1", by="price", now=NOW)
    assert tool.run(db(), category="chair", kind="food", now=NOW) == {"deals": 0}


def test_the_database_is_opened_read_only():
    path = db()
    before = open(path, "rb").read()
    tool.run(path, sku="K1", now=NOW)
    assert open(path, "rb").read() == before
