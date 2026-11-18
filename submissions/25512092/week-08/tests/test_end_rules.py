"""Sell to the end (person 11/12 ~16:20), end_rules.json read when limits is imported - through the real import path
in fresh processes (restock / the seller / the analyst each import limits): on -> restock stops 6 h before the end
(pricing.restock_stopped, what restock.main checks), the scheduled end cap and the automatic cap end at the same time
(limits.CAP_END_AT, read by depts/cap_brake and limits.auto_cap), the endgame markdown starts 12 h before the end
(pricing.endgame_step, the seller's floor); off -> 36 h / 11/15 23:59 / 48 h as before. Food unchanged.
    python tests/test_end_rules.py"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = """
import sys, json
sys.path.insert(0, r'%s')
from datetime import datetime as D
import limits, pricing
t = lambda *a: D(2026, *a).timestamp()
print(json.dumps({"stop_h": limits.RESTOCK_STOP_H, "cap_end": D.fromtimestamp(limits.CAP_END_AT).strftime('%%m-%%d %%H:%%M'),
    "endgame_h": pricing.ENDGAME_H, "food": D.fromtimestamp(limits.FOOD_TO_END_FROM).strftime('%%m-%%d %%H:%%M'),
    "stopped_1116_1200": pricing.restock_stopped(t(11, 16, 12, 0)), "stopped_1117_1800": pricing.restock_stopped(t(11, 17, 18, 0)),
    "endgame_1116_0100": pricing.endgame_step(t(11, 16, 1, 0)), "endgame_1117_1100": pricing.endgame_step(t(11, 17, 11, 0)),
    "endgame_1117_1230": pricing.endgame_step(t(11, 17, 12, 30))}))
""" % HERE


def run(rules):
    p = os.path.join(tempfile.mkdtemp(), "end_rules.json")
    if rules is not None:
        with open(p, "w") as f:
            json.dump(rules, f)
    env = dict(os.environ, W08_END_RULES=p, PYTHONUTF8="1")
    out = subprocess.run([sys.executable, "-c", CODE], capture_output=True, text=True, env=env, cwd=HERE, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_on_off():
    on = run({"enabled": True, "restock_stop_h": 6, "endgame_h": 12})
    assert on["stop_h"] == 6 and on["cap_end"] == "11-17 17:59" and on["endgame_h"] == 12, on
    assert not on["stopped_1116_1200"] and on["stopped_1117_1800"]
    assert on["endgame_1116_0100"] is None and on["endgame_1117_1100"] is None and on["endgame_1117_1230"] == 0, on
    assert on["food"] == "11-15 23:59"
    for off in (run(None), run({"enabled": False})):
        assert off["stop_h"] == 36 and off["cap_end"] == "11-15 23:59" and off["endgame_h"] == 48, off
        assert off["stopped_1116_1200"] and off["endgame_1116_0100"] == 0, off


if __name__ == "__main__":
    test_on_off()
    print("ok")
