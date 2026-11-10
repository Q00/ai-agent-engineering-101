"""forge_tool for the seller (user 11/10 21h; week-10 L3: when no tool fits, the agent makes one - description, code
and a test, run in a sandbox, listed only if it passes; LATM: a strong model makes the tool, cheap code uses it).

    py -m harness.forge            # one forge pass now (the runner: every 30 min)
    py -m harness.forge show       # the forged tools and their status

A pass: when no forged tool is in its A/B, take the states harness.jev marks 'no tool fits' (the rules settle under
12% there), show the strategy seat (opus) those states with their numbers and a few raw turns, and ask for ONE tool:
a pure function decide(v) and a test. Code then
1. checks the source (AST): no import, no open/exec/eval/compile, no dunder access - the tool sees only v;
2. runs the model's test and our own invariants in a separate process with a time limit (python -I): a counter
   never below v['floor'], an accept only of an offer at or over the floor, None or a valid move on 500 random views;
3. lists it as 'testing', installs it at once for every visit in its states (user 11/11: no A/B) and compares
   shop sales before/after at a 30-minute boundary (autopilot): success keeps it, anything else undoes and drops it.
Every move a forged tool makes still goes through act.* (check.price), so it cannot sell below cost.
"""
import ast
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402
from harness import out_dir  # noqa: E402

FORGED_DIR = HERE / "tools" / "forged"
TEST_RUN_MIN = 60             # the A/B's first slot ends at least this long after the start (autopilot extends it)
BANNED_CALLS = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals", "vars", "getattr",
                "setattr", "delattr", "breakpoint"}

ASK = """You make ONE new negotiation tool for a shop's seller agent. The seller's deterministic rules (open at a
share of list price, come down 1% of list per turn to a floor) fail in the states below: buyers leave without a deal.
Write a pure Python function that plays one turn in those states better.

Score the shop is judged on: 0.5 book (cash + stock at cost) + 0.3 revenue + 0.2 stars. A sale above the floor
beats a lost buyer; a sale below cost is impossible (the market tools refuse it).

The states (kind|list band|turn|buyer offer / our last ask|known buyer) where the rules settle under 12%:
{STATES}

A few of today's seller turns (our price, how the visit ended, its turns):
{EXAMPLES}

The function receives v, a dict: kind ('goods'|'food'), list_price, cost, floor (the lowest price we may take),
ask (our last ask or None), offer (the buyer's last offer or None), turn (1..), max_turns (12), known (bool),
name (str). It returns {"action": "counter", "price": int} | {"action": "accept"} | {"action": "reject"} | None
(None: let the rules play). Rules for the code: no imports, no I/O, no globals, deterministic, under 60 lines.
Never return a counter price below v["floor"]; accept only when v["offer"] >= v["floor"].

Answer with ONE JSON object and nothing else:
{"name": "<snake_case, starts with seller_>", "description": "<one line: what it does and when>",
 "states": [<the state strings from the list it is meant for>],
 "code": "def decide(v):\\n    ...",
 "test": "def test():\\n    ...   # asserts on decide() for 3-6 views from these states"}"""


def wisdom_active():
    from harness import wisdom
    return wisdom.active()


def _reg_path():
    return paths.memory() / "harness" / "forge" / "registry.json"


def registry():
    return atomic.read_json(_reg_path(), default=None) or {}


def _save(reg):
    atomic.write_json(_reg_path(), reg)


def check_source(code):
    """Reasons the code may not run, or []."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax: {e}"]
    why = []
    if not any(isinstance(n, ast.FunctionDef) and n.name == "decide" for n in tree.body):
        why.append("no top-level def decide(v)")
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            why.append("imports are not allowed")
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            why.append("global/nonlocal are not allowed")
        elif isinstance(n, ast.Attribute) and n.attr.startswith("__"):
            why.append(f"dunder access {n.attr}")
        elif isinstance(n, ast.Name) and (n.id in BANNED_CALLS or n.id.startswith("__")):
            why.append(f"name {n.id} is not allowed")
    return sorted(set(why))


INVARIANTS = r'''
import random
random.seed(7)
bad = []
for i in range(500):
    lp = random.choice([9000, 25000, 80000, 250000, 700000, 2500000])
    cost = int(lp * random.uniform(0.4, 0.75))
    floor = int(max(cost * 1.1, lp * random.uniform(0.5, 0.9)))
    ask = random.choice([None, int(lp * random.uniform(0.6, 1.0))])
    offer = random.choice([None, int(lp * random.uniform(0.2, 1.0))])
    v = {"kind": random.choice(["goods", "food"]), "list_price": lp, "cost": cost, "floor": floor, "ask": ask,
         "offer": offer, "turn": random.randint(1, 12), "max_turns": 12, "known": random.random() < 0.5, "name": "x"}
    m = decide(dict(v))
    if m is None:
        continue
    a = m.get("action") if isinstance(m, dict) else None
    if a == "counter":
        p = m.get("price")
        if not isinstance(p, int) or p < floor:
            bad.append(("counter below floor or not int", v, m))
    elif a == "accept":
        if offer is None or offer < floor:
            bad.append(("accept under floor", v, m))
    elif a != "reject":
        bad.append(("bad action", v, m))
assert not bad, bad[:3]
test()
print("ok")
'''


def sandbox(code, test, timeout=10):
    """(ok, output) from running the tool, the model's test and our invariants in their own process."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.py"
        f.write_text(code + "\n\n" + test + "\n\n" + INVARIANTS, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", str(f)], capture_output=True, text=True, timeout=timeout, cwd=d)
        except subprocess.TimeoutExpired:
            return False, "timed out"
    out = (r.stdout + r.stderr)[-800:]
    return r.returncode == 0 and "ok" in r.stdout, out


def _examples(states, per=3):
    from harness import jev, trajectory
    want, rows = set(states), []
    for t in trajectory.build(time.strftime("%Y%m%d")).values():
        if t["role"] != "seller":
            continue
        for s in t["steps"]:
            if s.get("kind") == "seller.decision" and len(rows) < per * len(want):
                rows.append({"our_price": s.get("price"), "outcome": t["result"].get("outcome"),
                             "turns": t["result"].get("turns")})
    return rows[: per * len(want)]


def _json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


def forge_once(now=None, call=None):
    """One pass. Returns what happened."""
    from harness import autopilot, experiment, jev
    now = now or time.time()
    reg = registry()
    sync(reg, now)
    if any(r.get("status") == "testing" for r in reg.values()):
        return {"skipped": "a forged tool is in its A/B"}
    states = [r for r in jev.no_fit_states() if r["state"] not in {s for x in reg.values() for s in x.get("states", [])}][:8]
    if not states:
        return {"skipped": "no state without a fitting tool"}
    if call is None:
        from agents import model
        call = lambda prompt: model.call("strategy", prompt, system="Answer in the form asked, nothing else.",  # noqa: E731
                                         timeout=240)
    prompt = ASK.replace("{STATES}", json.dumps(states, ensure_ascii=False, indent=0)).replace(
        "{EXAMPLES}", json.dumps(_examples([s["state"] for s in states])))
    a = _json(call(prompt))
    name = re.sub(r"[^a-z0-9_]", "_", str(a.get("name") or "seller_forged").lower())[:40]
    if not name.startswith("seller_"):
        name = "seller_" + name
    if name in reg:
        name += time.strftime("_%H%M", time.localtime(now))
    code, test = a.get("code") or "", a.get("test") or "def test():\n    pass"
    why = check_source(code) + [f"test: {w}" for w in check_source(test.replace("decide(", "_d("))
                                 if "no top-level" not in w]
    rec = {"role": "seller", "description": str(a.get("description"))[:200], "made": now,
           "states": [s for s in (a.get("states") or []) if s in {x["state"] for x in states}] or [states[0]["state"]],
           "by": "strategy seat (opus)"}
    if why:
        rec.update(status="refused", why=why)
        reg[name] = rec
        _save(reg)
        return {"refused": name, "why": why}
    ok, out = sandbox(code, test)
    if not ok:
        rec.update(status="failed test", why=out[-300:])
        reg[name] = rec
        _save(reg)
        return {"failed": name, "out": out[-300:]}
    FORGED_DIR.mkdir(parents=True, exist_ok=True)
    (FORGED_DIR / f"{name}.py").write_text(f'"""{rec["description"]}\n\nForged {time.strftime("%Y-%m-%d %H:%M", time.localtime(now))} '
                                           f'by harness.forge for states {rec["states"]}."""\n\n{code}\n',
                                           encoding="utf-8")
    (FORGED_DIR / f"{name}_test.py.txt").write_text(test, encoding="utf-8")
    exp_id = f"f-{name}"
    change = {"playbook": {"seller": {"forged": name}}}
    # user 11/11 00:2x: no A/B - the tool goes in for every visit in its states at once, before/after decides
    prev = ((wisdom_active().get("playbook") or {}).get("seller") or {}).get("forged") or ""
    installed = autopilot._install(f"forged tool {name}", change, f"forge: passed sandbox and invariants; states "
                                   f"{rec['states']}", "sales", now).get("installed")
    experiment.register(exp_id, change, "sales", {"direction": "up", "by": 0.05},
                        TEST_RUN_MIN / 60, "before_after", why=f"forged tool {name}: {rec['description']}", now=now)
    experiment.approve(exp_id, "forge: passed sandbox and invariants", now=now)
    led = experiment.ledger()
    led["experiments"][exp_id].update(end=autopilot.slot_end(now + (TEST_RUN_MIN - 25) * 60), autopilot=True,
                                      extends=0, undo={"playbook.seller.forged": prev}, request=None,
                                      installed=installed)
    atomic.write_json(experiment.path(), led)
    rec.update(status="testing", experiment=exp_id)
    reg[name] = rec
    _save(reg)
    return {"testing": name, "states": rec["states"], "experiment": exp_id}


def sync(reg=None, now=None):
    """Registry status from the experiment ledger: an A/B that ended in 'success, adopted' adopts the tool."""
    from harness import experiment
    reg = registry() if reg is None else reg
    led = experiment.ledger()["experiments"]
    changed = False
    for name, r in reg.items():
        e = led.get(r.get("experiment") or "")
        if r.get("status") != "testing" or not e or e["status"] == "running":
            continue
        why = (e.get("stopped") or {}).get("why") or e.get("verdict") or e["status"]
        r.update(status="adopted" if str(why).startswith("success") else "dropped", verdict=why, settled=now or time.time())
        changed = True
    if changed:
        _save(reg)
    return reg


if __name__ == "__main__":
    if sys.argv[1:2] == ["show"]:
        print(json.dumps(sync(), ensure_ascii=False, indent=1, default=str))
    else:
        print(json.dumps(forge_once(), ensure_ascii=False, indent=1, default=str))
