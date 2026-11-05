"""Two files under the shop's logs/strategist/, which the log tunnel serves next to logs/decisions/:

- policy.json: the policy now on disk, its version and time, numbers and on/off switches only;
- changes.md: the strategist's last changes, newest first: keys, old and new numbers, the kind of change
  (model, market-rule, bounds, migration, linked, revert, focus, trend) and the outcome check's result.

Anyone with the tunnel URL can read them, so they hold numbers only: no reasons, assessments, notes, prompts,
briefs or model answers (those stay in the strategist's home), and the focus and trend SKU lists only as counts.
"""
import json
import os
import time
from pathlib import Path

from memory.store import tail_jsonl
from strategist import skillfile

MAX_ROWS = 150
LISTS = ("stock.focus_skus", "stock.trend_skus")


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _flat(d, prefix=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        elif isinstance(v, (int, float)):           # bool included: the on/off switches
            out[f"{prefix}{k}"] = v
    return out


def _num(v):
    return json.dumps(v) if isinstance(v, (int, float)) else "-"


def _outcome(o):
    if not o:
        return "-"
    ms = ", ".join(f"{m} {_num(b)}→{_num(a)}" for m, (b, a) in (o.get("metrics") or {}).items()
                   if isinstance((o.get("metrics") or {}).get(m), list))
    keys = o.get("reverted") or []
    return f"{o.get('result')}" + (f" ({', '.join(keys)})" if keys else "") + (f": {ms}" if ms else "")


def changes_md(rows):
    """The table: change rows (with a diff) newest first, each joined with the outcome row of its run."""
    outcomes = {r.get("outcome_of"): r for r in rows if isinstance(r, dict) and r.get("outcome_of")}
    out = ["# Strategist changes", "", "| time | run | version | change | kind | outcome |", "|---|---|---|---|---|---|"]
    changes = [r for r in rows if isinstance(r, dict) and isinstance(r.get("diff"), dict)][-MAX_ROWS:]
    for r in reversed(changes):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])) if isinstance(r.get("ts"), (int, float)) else "-"
        kinds = r.get("kind") if isinstance(r.get("kind"), dict) else {}
        model = any(kinds.get(k, "model") == "model" for k in r["diff"] if k not in LISTS)
        o = _outcome(outcomes.get(r.get("run_id"))) if model else "-"
        if model and o == "-" and kinds:                # a row without kinds is from before the outcome checks
            o = "pending"
        for k, d in r["diff"].items():
            if not isinstance(d, list) or len(d) != 2:
                continue
            if k in LISTS:
                old, new = (set(x) if isinstance(x, list) else set() for x in d)
                change = f"{k} +{len(new - old)} -{len(old - new)}"
            else:
                change = f"{k} {_num(d[0])}→{_num(d[1])}"
            kind = kinds.get(k, "model")
            out.append(f"| {when} | {r.get('run_id')} | {r.get('old_ver')}→{r.get('new_ver')} | {change} "
                       f"| {kind} | {o if kind == 'model' else '-'} |")
    return "\n".join(out) + "\n"


def write(shop_home, home, skill_path, now):
    """Write both files. Raises on a write error (run.py records it)."""
    d = Path(shop_home) / "logs" / "strategist"
    text, front, _, errors = skillfile.read(skill_path)
    if not errors and isinstance(front.get("policy"), dict):
        _write(d / "policy.json", json.dumps({"ver": skillfile.ver(text), "ts": now,
                                              "policy": _flat(front["policy"])}, indent=1) + "\n")
    rows = tail_jsonl(Path(home) / "changes.jsonl", 4 * MAX_ROWS)
    _write(d / "changes.md", changes_md(rows))
