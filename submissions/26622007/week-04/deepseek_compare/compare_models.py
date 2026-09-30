"""Audit the DeepSeek suites against their logs and put them next to the Luna suites, design by design."""
import argparse
from collections import Counter
import csv
from decimal import Decimal
import hashlib
import json
import re

import run_deepseek as ds

# By path: several experiment folders have a compare.py, and sys.path order depends on import order.
luna_compare = ds.luna.load_module("armed_compare", ds.ROOT / "armed_tool/compare.py")

lab = ds.base_lab
GUN = luna_compare.WEAPON_WORDS
LUNA_SUITES = {"shotgun-auto": "shotgun-auto-luna-20260928", "shotgun-forced": "shotgun-forced-luna-20260928",
               "holding-with-tool": "holding-with-tool-luna-20260928", "holding-only": "holding-only-luna-20260928"}


def events_of(run, scenario=None):
    """Luna logs are one file per run; DeepSeek episode-parallel logs are one file per episode."""
    name = f"{run}-s{scenario}.jsonl" if run.startswith("deepseek-") else f"{run}.jsonl"
    return [json.loads(line) for line in (ds.ROOT / "logs" / name).read_text().splitlines()]


def audit(rows, manifest):
    sides = manifest["armed"]
    assert len(rows) == 36 * len(sides) and len({(r["run"], r["scenario"]) for r in rows}) == len(rows)
    counts = Counter((r["armed_role"], r["condition"], r["scenario"]) for r in rows)
    assert all(counts[(a, c, str(s["id"]))] == 3 for a in sides for c in lab.CONDITIONS for s in manifest["scenarios"])
    for path, digest in manifest["inputs"].items():
        assert hashlib.sha256((ds.ROOT / path).read_bytes()).hexdigest() == digest, path
    config, errors, episodes = manifest["config"], Counter(), {}
    choices = ("auto", "none", manifest.get("forced_choice"))
    for row in rows:
        run = row["run"]
        side = run.split("-")[-3]
        for e in events_of(run, row["scenario"]):
            if e["scenario"] is not None:
                episodes.setdefault((run, str(e["scenario"])), []).append(e)
            if e["event"] == "request":
                p = e["payload"]
                for field in ("model", "temperature", "top_p", "reasoning", "provider"):
                    assert p[field] == config[field], (run, field)
                assert ("tools" in p) == (manifest["tool"] is not None and e["contractor"] == side), (run, e["contractor"])
                if "tools" in p:
                    assert p["tools"] == [manifest["tool"]] and p["tool_choice"] in choices, run
                if e["contractor"] != "reader":
                    holds = ds.holding.HOLDING in p["messages"][0]["content"]
                    assert holds == ("holding_sentence" in manifest and e["contractor"] == side), (run, e["contractor"])
            elif e["event"] == "response":
                data = json.loads(e["raw_response"])
                assert data["model"].startswith(config["model"]) and data["provider"] == "DeepInfra", run
            elif e["event"] in ("http_error", "transport_error"):
                errors[str(e.get("status", "transport"))] += 1
    for row in rows:
        events = episodes[(row["run"], row["scenario"])]
        result = [e["result"] for e in events if e["event"] == "episode_result"]
        assert len(result) == 1 and all(str(result[0][k]) == row[k] for k in row), (row["run"], row["scenario"])
        if row["status"] != "crashed":
            assert sum(e["event"] == "message" for e in events) == int(row["turns"])
        assert sum(e["event"] == "tool_call" for e in events) == int(row["tool_calls"])
        assert sum(e["event"] == "request" for e in events) == int(row["http_requests"])
    return errors, episodes


def speech(rows):
    """Per episode set: holder/other messages naming the gun, and empty messages."""
    out = Counter(dict.fromkeys(("messages", "empty_messages", "holder_gun_messages", "other_gun_messages"), 0))
    for r in rows:
        side = r["armed_role"]
        for e in events_of(r["run"], r["scenario"]):
            if str(e["scenario"]) != r["scenario"] or e["event"] != "message":
                continue
            out["messages"] += 1
            out["empty_messages"] += not e["text"].strip()
            key = "holder_gun_messages" if e["speaker"] == side else "other_gun_messages"
            out[key] += bool(GUN.search(e["text"]))
    return out


def line(model, design, side, rows, scenarios):
    s = [x for x in luna_compare.summarize(rows, f"{model}:{design}:{side}", scenarios) if x["condition"] == "all"][0]
    own = {"buyer": s["buyer_share"], "seller": s["seller_share"]}.get(side, "")
    return {"model": model, "design": design, "armed": side, "episodes": s["episodes"], "correct": s["correct"],
            "deal": s["deal"], "no_deal": s["no_deal"], "open": s["open"], "crashed": s["crashed"],
            "below_reserve": s["below_reserve"], "above_budget": s["above_budget"],
            "buyer_share": s["buyer_share"], "seller_share": s["seller_share"], "armed_share": own,
            "tool_used": s["tool_used"], "tool_calls": s["tool_calls"], "mean_turns": s["mean_turns"],
            "format_errors": s["format_errors"], **speech(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    table, report = [], {}
    scenarios = {str(s["id"]): s for s in json.loads((ds.ROOT / "scenarios.json").read_text())}
    luna_control = [r for r in luna_compare.read_rows(luna_compare.CONTROL / "results.csv") if r["reasoning_effort"] == "low"]
    table.append(line("luna", "control", "none", [{**r, "armed_role": "none"} for r in luna_control], scenarios))
    for design in ds.DESIGNS:
        suite = ds.suite_name(design)
        out = ds.HERE / "runs" / suite
        manifest = json.loads((out / "manifest.json").read_text())
        rows = luna_compare.read_rows(out / "results.csv")
        errors, episodes = audit(rows, manifest)
        report[suite] = {"episodes": len(rows), "http_errors": dict(errors),
                         "cost_usd": f"{sum(Decimal(r['cost_usd'] or '0') for r in rows):.6f}"}
        (out / "audit.json").write_text(json.dumps(report[suite], ensure_ascii=False, indent=2) + "\n")
        for side in manifest["armed"]:
            if design != "control":
                luna_rows = [r for r in luna_compare.read_rows(luna_compare.armed.HERE / "runs" / LUNA_SUITES[design] / "results.csv")
                             if r["armed_role"] == side]
                table.append(line("luna", design, side, luna_rows, scenarios))
            table.append(line("deepseek", design, side, [r for r in rows if r["armed_role"] == side], scenarios))
    luna_compare.write_csv(ds.HERE / "runs" / "model-comparison.csv", table)
    cols = list(table[0])
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for t in table:
        print("| " + " | ".join(str(t[c]) for c in cols) + " |")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
