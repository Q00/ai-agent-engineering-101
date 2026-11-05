"""capability.list (design 6, R7, D-27, C3): what each role can do, and how far the meta-harness may move it.

    py -m harness.capability              # the list, as the meta-harness sees it

Two parts:
- capability (stated, source code:): the tools each caller may call - the model's list in agents/loop.py ROLES plus
  the tools the role's rules call (RULE_TOOLS, checked against the 11/4-11/5 tool records) - and the playbook numbers
  the role's code actually reads, found in the code itself, with the schema's range;
- allowed range (wisdom, source user:): how far each number may move, from memory/harness/allowed.json, written only
  through the approval path. A number with no approved range is "not approved yet": the meta-harness may not propose
  it outside its current value.
permitted() is what tools.registry.call checks (C3). Until ENFORCE is set it only marks the record.
"""
import json
import re

from core import atomic, paths, schema
from harness import out_dir

ENFORCE = False          # design 20-2 T4: one day of marking first, then refusing (the user's call)

# The tools each role's rules call without a model (11/4-11/5 tool records, by caller)
RULE_TOOLS = {
    "seller": ["calc.extract", "calc.guess_counterpart", "check.price", "me.inventory", "act.counter", "act.accept",
               "act.reject"],
    "buyer": ["calc.extract", "me.counterpart", "check.receipt", "act.pick", "act.offer", "act.finish",
              "act.walk_away", "market.sku"],
    "restock": ["me.inventory", "me.missed", "market.selling", "market.forum", "act.buy", "check.buy"],
    "review": ["calc.fact_stars", "me.counterpart", "act.review"],
    "forum": ["market.forum", "me.inventory", "act.post", "act.vote"],
}
# Callers that are not trading roles (the outer runtime's analysts, checks, dry runs): read tools and their own rec.*
OUTER_CALLERS = ("outer", "audit", "strategy", "check", "dryrun", "meta", "test")
# Where a role's numbers are read: the role's own code and the tools that enforce its limits
ROLE_FILES = {"seller": ["agents/seller_core.py", "tools/act.py"], "buyer": ["agents/buyer_core.py"],
              "restock": ["restock.py", "tools/check.py", "tools/act.py"], "review": ["review.py", "tools/calc.py"],
              "hold": ["tools/market_files.py"]}
_ALIAS = r"""(?:playbook\([^)]*\)|\bpb|\brv|\bh)"""
_KEY = r"""\s*(?:\[\s*["'](\w+)["']\s*\]|\.get\(\s*["'](\w+)["'])"""


def tools_for(caller):
    from agents import loop
    r = loop.ROLES.get(caller) or {}
    return sorted(set(r.get("look", [])) | set(r.get("final", [])) | set(RULE_TOOLS.get(caller, [])))


def permitted(caller, tool):
    """(ok, why) for C3. Outer callers may read anything and write only rec.*; a trading role only its list."""
    if caller in OUTER_CALLERS or caller.endswith("analyst"):
        if tool.startswith("act."):
            return False, f"{caller} is not a trading role"
        return True, None
    allowed = tools_for(caller)
    if not allowed:
        return False, f"no capability list for caller {caller!r}"
    return (True, None) if tool in allowed else (False, f"{tool} is not in {caller}'s capability list")


def numbers_read(role):
    """The playbook keys the role's code reads (stated: our code). Two patterns: (...get("<role>") or {}).get("x")
    anywhere - exact, kept as found; and a local alias (pb["x"], rv.get("x")) - loose, so kept only when the schema
    has the key."""
    props = set((schema.STRATEGY["properties"]["playbook"]["properties"].get(role) or {}).get("properties") or {})
    exact = re.compile(r"""\.get\(\s*["']%s["']\s*\)\s*or\s*\{\}\s*\)""" % role + _KEY)
    loose = re.compile(_ALIAS + _KEY)
    keys = set()
    for f in ROLE_FILES.get(role, []):
        p = paths.root() / f
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        keys |= {a or b for a, b in exact.findall(text)}
        keys |= {a or b for a, b in loose.findall(text)} & props
    return keys


def allowed_ranges():
    return atomic.read_json(paths.memory() / "harness" / "allowed.json", default=None) or {}


def capability_list():
    pb_schema = schema.STRATEGY["properties"]["playbook"]["properties"]
    active = atomic.read_json(paths.memory() / "strategy" / "active.json", default={}) or {}
    current = active.get("playbook") or {}
    ranges = allowed_ranges()
    out = {"tools": {r: tools_for(r) for r in RULE_TOOLS}, "numbers": {}}
    for role, sch in pb_schema.items():
        props = sch.get("properties") or {}
        read = numbers_read(role)
        rows = {}
        for k, v in props.items():
            ok = (ranges.get(role) or {}).get(k)
            rows[k] = {"read_by_code": k in read, "schema": {x: v[x] for x in ("type", "minimum", "maximum", "enum")
                                                             if x in v},
                       "current": (current.get(role) or {}).get(k), "allowed": ok or "not approved yet"}
        out["numbers"][role] = {"rows": rows, "unused_in_schema": sorted(k for k in props if k not in read),
                                "read_not_in_schema": sorted(read - set(props))}
    return out


def write():
    p = out_dir() / "capability.json"
    atomic.write_json(p, capability_list())
    return p


if __name__ == "__main__":
    c = capability_list()
    for role, n in c["numbers"].items():
        print(role, "read:", sorted(k for k, r in n["rows"].items() if r["read_by_code"]),
              "| in schema, not read:", n["unused_in_schema"], "| read, not in schema:", n["read_not_in_schema"])
    print(json.dumps(c["tools"], indent=1))
