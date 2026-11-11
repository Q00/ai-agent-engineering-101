"""The market's rules as top-level wisdom (user 11/11 13h: "rule changes should have been taken in as wisdom on their
own - find why not, and make it automatic, at the top").

Why patch 3.4's surge cost never reached a decision: harness.rules_watch saw the change (11/6 09:34) but only kept a
copy, wrote one knowledge item with no content ("the rules text as of ...") and sent an alert that went nowhere (no
Telegram on this PC). No model ever read the rules: the meta context had the score formula and our design rules, the
role cards a hand-written 11/5 summary. And a rule that needs new data read (inventory.json 'surge') was out of reach
of the only lever the meta-harness has, playbook numbers.

    py -m harness.rules_digest          # refresh now if the rules changed since the last digest (meta does it first)
    py -m harness.rules_digest --force  # refresh now

ensure(): when the rules' hash differs from the digest's, the analyst seat reads the whole rules page and agent.md
and lists every rule that moves money, score, limits, timing or the data we get - each with the data field it
names. Code then checks whether our code reads that field (a grep over the trading code) and marks the rule
'handled' or 'not followed by our code'. The digest goes:
- first into every role's card (core.card.assemble) and the meta context, above everything else;
- into knowledge as stated items (market-rule:<id>, source market:rules@hash);
- unfollowed rules into the meta context as the first thing to fix, onto the status page, and as an alert.
"""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402

CODE_GLOBS = ("restock.py", "seller.py", "market_buyer.py", "agents/*.py", "tools/*.py")
MAX_RULE_CHARS = 60000

ASK = """You read a trading market's own rules (its rules page and agent.md, below) for an agent that runs a shop
and a buyer there. List EVERY rule that changes money, the score, limits, timing, or the data the agent gets -
including patch notes. Be exact: copy the numbers. Do not invent anything not in the text.

Answer with ONE JSON object and nothing else:
{"rules": [{"id": "<short snake_case>", "rule": "<one sentence with the exact numbers>",
            "applies_to": ["shop"|"seller"|"buyer"|"restock"|"forum"|"score"],
            "data_field": "<the file/field the market gives for it, e.g. inventory.json surge, or null>",
            "must_do": "<what an agent must do differently because of it, one sentence>",
            "patch": "<patch number if the text gives one, else null>"}]}

# rules page
{RULES}

# agent.md
{AGENT}"""


def _path():
    return paths.memory() / "harness" / "rules_digest.json"


def digest():
    return atomic.read_json(_path(), default=None) or {}


def _rules_state():
    return atomic.read_json(paths.memory() / "market" / "rules" / "state.json", default=None) or {}


def _code_text():
    out = []
    for g in CODE_GLOBS:
        for f in HERE.glob(g):
            try:
                out.append(f.read_text(encoding="utf-8"))
            except OSError:
                pass
    return "\n".join(out)


def handled(rule, code):
    """Does our code read the field the rule names? None when the rule names no field."""
    f = rule.get("data_field")
    if not f or not re.search(r"\.json|/api/", str(f)):
        return None                                  # a rule with no file or API field is for the reader, not code
    # the first field the rule names after its file (inventory.json surge -> surge): does any code read it by name
    stop = {"json", "jsonl", "inventory", "deal", "shops", "forum", "outbox", "payments", "demand", "catalog", "order",
            "market", "the", "and", "get", "api", "response", "vote", "board"}
    names = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(f)) if w.lower() not in stop]
    return bool(names) and bool(re.search(rf"[\"']{re.escape(names[0])}[\"']", code))


def ensure(now=None, force=False, call=None):
    """Refresh the digest when the rules changed. Returns what happened."""
    now = now or time.time()
    st = _rules_state()
    key = "|".join(str((st.get(d) or {}).get("hash")) for d in ("rules", "agent"))
    cur = digest()
    if cur.get("key") == key and not force:
        return {"fresh": True, "rules": len(cur.get("rules", [])), "gaps": len(gaps(cur))}
    rd = paths.memory() / "market" / "rules"
    texts = {d: (rd / st[d]["file"]).read_text(encoding="utf-8") if (st.get(d) or {}).get("file") else ""
             for d in ("rules", "agent")}
    if call is None:
        from agents import model
        call = lambda p: model.call("analyst", p, system="Answer in the form asked, nothing else.", timeout=300)  # noqa: E731
    prompt = ASK.replace("{RULES}", texts["rules"][:MAX_RULE_CHARS]).replace("{AGENT}", texts["agent"][:MAX_RULE_CHARS])
    m = re.search(r"\{.*\}", call(prompt) or "", re.S)
    rules = (json.loads(m.group(0)) if m else {}).get("rules") or []
    code = _code_text()
    for r in rules:
        r["handled"] = handled(r, code)
    doc = {"key": key, "made": now, "patch": (st.get("rules") or {}).get("patch"), "rules": rules,
           "source": {d: (st.get(d) or {}).get("file") for d in ("rules", "agent")}}
    atomic.write_json(_path(), doc)
    from harness import alert, knowledge
    for r in rules:
        knowledge.write({"id": f"market-rule:{r.get('id')}", "about": "market:rule", "claim": str(r.get("rule"))[:400],
                         "source": f"market:rules@{(st.get('rules') or {}).get('hash')}",
                         "method": "harness.rules_digest (analyst reading the market's own text)",
                         "links": [f"memory/market/rules/{doc['source'].get('rules')}"]}, now)
    g = gaps(doc)
    if g:
        alert.send("[규칙 미반영] 우리 코드가 따르지 않는 시장 규칙 " + str(len(g)) + "건:\n"
                   + "\n".join(f"- {r['id']}: {r['rule'][:120]}" for r in g[:8]), "now", now)
    return {"refreshed": True, "rules": len(rules), "gaps": len(g)}


def gaps(doc=None):
    doc = digest() if doc is None else doc
    return [r for r in doc.get("rules", []) if r.get("handled") is False]


def text(limit=6000):
    """The block that goes first into every card and the meta context."""
    doc = digest()
    if not doc.get("rules"):
        return ""
    lines = [f"# market rules in force (stated, read from the market's own pages, patch {doc.get('patch')}, "
             f"{time.strftime('%m-%d %H:%M', time.localtime(doc['made']))}) - these come before anything below; "
             "where our strategy or habits disagree, the rule wins"]
    for r in doc["rules"]:
        flag = "  [OUR CODE DOES NOT FOLLOW THIS YET]" if r.get("handled") is False else ""
        lines.append(f"- {r.get('rule')}" + (f" (data: {r['data_field']})" if r.get("data_field") else "") + flag)
    out = "\n".join(lines)
    return out if len(out) <= limit else out[: limit - 1] + "…"


if __name__ == "__main__":
    print(json.dumps(ensure(force="--force" in sys.argv), ensure_ascii=False, indent=1))
