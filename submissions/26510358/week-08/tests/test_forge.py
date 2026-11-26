"""RL_PROACTIVE.md 2.3: a tool is registered only after the static screen and its own tests in a sandbox without
network or keys, at most 3 tries with the failure fed back; a registered tool is reloaded on the next run only
while its file matches the recorded hash; policy tools need a shadow_gate PASS, money tools a person."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import forge, react

GOOD = '''import sqlite3


def run(db_path, sku=None):
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {"deals": c.execute("SELECT COUNT(*) FROM settles WHERE sku = ?", (sku,)).fetchone()[0]}
    finally:
        c.close()
'''
TESTS = '''import sqlite3
import tool


def test_counts():
    c = sqlite3.connect("t.sqlite")
    c.execute("CREATE TABLE settles (sku TEXT)")
    c.executemany("INSERT INTO settles VALUES (?)", [("A",), ("A",), ("B",)])
    c.commit()
    c.close()
    assert tool.run("t.sqlite", sku="A") == {"deals": 2}
'''


def test_the_screen_refuses_network_files_processes_secrets_and_writable_databases():
    bad = {"import socket\ndef run(db):\n    return 1\n": "import socket",
           "import os\ndef run(db):\n    return os.environ\n": "import os",
           "from urllib import request\ndef run(db):\n    return 1\n": "import urllib",
           "def run(db):\n    return open('/etc/passwd').read()\n": "open is not allowed",
           "def run(db):\n    return __import__('os')\n": "__import__",
           "def run(db):\n    return (1).__class__\n": "attribute __class__",
           "def run(db):\n    return 'ops/deploy_pull.py'\n": "mentions 'ops/'",
           "import sqlite3\ndef run(db):\n    return sqlite3.connect(db)\n": "read-only",
           "def helper():\n    return 1\n": "must define run"}
    for code, reason in bad.items():
        assert any(reason in w for w in forge.screen(code)), (code, forge.screen(code))
    assert forge.screen(GOOD) == []


def test_tests_run_without_network_and_without_our_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    probe = '''import os, socket
import tool


def test_no_key_in_the_environment():
    assert "OPENAI_API_KEY" not in os.environ


def test_no_network():
    try:
        socket.create_connection(("example.com", 80), timeout=1)
    except OSError as e:
        assert "disabled" in str(e)
    else:
        raise AssertionError("network reachable")
'''
    ok, out = forge.sandbox_test("def run(db):\n    return 1\n", probe)
    assert ok, out
    ok, out = forge.sandbox_test("def run(db):\n    return 1\n", "import tool\n\ndef test_wrong():\n    assert tool.run(0) == 2\n")
    assert not ok and "FAIL test_wrong" in out
    assert not forge.sandbox_test("def run(db):\n    return 1\n", "import tool\n")[0]          # no test is not a pass


def test_a_failed_proposal_gets_the_failure_back_and_three_tries_at_most(tmp_path):
    seen = []

    def propose(feedback):
        seen.append(feedback)
        return (GOOD if len(seen) == 2 else GOOD.replace("sku = ?", "sku != ?")), TESTS
    meta = forge.forge("count_deals", propose, "deals of a SKU", args={"sku": "str"}, uses=["unknown"], out_dir=tmp_path)
    assert meta["registered"] and meta["verified"]["tries"] == 2 and meta["verified"]["tests"] == "1 passed, 0 failed"
    assert seen[0] is None and "FAIL test_counts" in seen[1]
    assert (tmp_path / "count_deals.py").read_text() == GOOD and (tmp_path / "count_deals.tests.py").exists()

    never = forge.forge("never", lambda fb: (GOOD.replace("sku = ?", "sku != ?"), TESTS), "x", out_dir=tmp_path)
    assert never["registered"] is False and len(never["tries"]) == forge.MAX_TRIES
    assert not (tmp_path / "never.py").exists()


def test_registered_tools_reload_for_react_until_the_file_changes(tmp_path):
    forge.forge("count_deals", lambda fb: (GOOD, TESTS), "deals of a SKU", args={"sku": "str"},
                uses=["forum_post"], out_dir=tmp_path)
    db = tmp_path / "m.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE settles (sku TEXT)")
    c.execute("INSERT INTO settles VALUES ('G1')")
    c.commit()
    c.close()
    tools = forge.load_tools(db, "forum_post", out_dir=tmp_path)
    assert [t.name for t in tools] == ["count_deals"] and isinstance(tools[0], react.Tool)
    assert json.loads(react._observe(tools[0], {"sku": "G1"})) == {"deals": 1}
    assert forge.load_tools(db, "restock_review", out_dir=tmp_path) == []           # not an event it serves
    (tmp_path / "count_deals.py").write_text(GOOD + "\n# edited after verification\n")
    assert forge.load_tools(db, out_dir=tmp_path) == []                             # hash no longer matches


def test_policy_tools_need_the_shadow_gate_and_money_tools_a_person(tmp_path):
    kw = dict(description="x", out_dir=tmp_path)
    p = forge.forge("pol", lambda fb: (GOOD, TESTS), kind="policy", **kw)
    assert p["registered"] and not p["loadable"] and "shadow_gate" in p["not_loaded_because"]
    m = forge.forge("pay", lambda fb: (GOOD, TESTS), moves_money=True, **kw)
    assert not m["loadable"] and "approval" in m["not_loaded_because"]
    assert forge.load_tools(tmp_path / "none.sqlite", out_dir=tmp_path) == []
    ok = forge.forge("pol2", lambda fb: (GOOD, TESTS), kind="policy", shadow_gate={"verdict": "PASS"}, **kw)
    assert ok["loadable"] and [t.name for t in forge.load_tools(tmp_path / "none.sqlite", out_dir=tmp_path)] == ["pol2"]


def test_the_first_tool_is_registered_and_given_to_both_react_agents():
    names = [m["name"] for m in forge.registry()]
    assert "ledger_query" in names
    meta = next(m for m in forge.registry() if m["name"] == "ledger_query")
    assert meta["kind"] == "read" and not meta["moves_money"] and meta["loadable"]
    assert {"forum_post", "restock_review"} <= set(meta["uses"])
    assert "ledger_query" in [t.name for t in forge.tools_for("forum_post")]
