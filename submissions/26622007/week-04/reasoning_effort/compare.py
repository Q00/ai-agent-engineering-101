"""Audit the Luna suite against its raw logs and write the effort/model comparison tables."""
import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal
import hashlib
import json
from statistics import mean, median

import run_luna as luna
import experiment as english  # the untouched 8-message module, for the prefix replay

lab = luna.lab


def read_rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader(); w.writerows(rows)


def outcome_summary(rows, label):
    out = []
    for c in lab.CONDITIONS:
        g = [r for r in rows if r["condition"] == c]
        out.append({"group": label, "condition": c, "episodes": len(g),
                    "correct": sum(int(r["correct"] or 0) for r in g),
                    **{x: sum(r["outcome"] == x for r in g) for x in ("deal", "no_deal", "open")},
                    "crashed": sum(r["outcome"] == "" for r in g),
                    "violations": sum(int(r["violation"] or 0) for r in g),
                    "mean_turns": round(mean(int(r["turns"]) for r in g), 2),
                    "max_turns": max(int(r["turns"]) for r in g),
                    "format_errors": sum(int(r["format_errors"]) for r in g),
                    "reader_calls": sum(int(r["reader_calls"]) for r in g)})
    return out


def audit(rows, manifest):
    """Every row must be reproducible from its log; every request must carry the fixed settings."""
    assert len(rows) == 72 and len({(r["run"], r["scenario"]) for r in rows}) == 72
    counts = Counter((r["reasoning_effort"], r["condition"], r["scenario"]) for r in rows)
    assert all(counts[(e, c, str(s["id"]))] == 3 for e in luna.EFFORTS for c in lab.CONDITIONS for s in manifest["scenarios"])
    for path, digest in manifest["inputs"].items():
        assert hashlib.sha256((luna.ROOT / path).read_bytes()).hexdigest() == digest, path
    calls, schema = [], {"checked": 0, "invalid": []}
    errors, episodes = Counter(), {}
    for run in sorted({r["run"] for r in rows}):
        effort, condition = run.split("-")[-3], run.split("-")[-2]
        config = manifest["configs"][effort]
        numbered = [(i, json.loads(line)) for i, line in enumerate((luna.ROOT / "logs" / f"{run}.jsonl").read_text().splitlines(), 1)]
        pending = None
        for line, e in numbered:
            if e["scenario"] is not None:
                episodes.setdefault((run, str(e["scenario"])), []).append(e)
            if e["event"] == "request":
                p, role = e["payload"], e["contractor"]
                for field in ("model", "reasoning", "provider"):
                    assert p[field] == config[field], (run, line, field)
                assert "temperature" not in p and "top_p" not in p and "max_tokens" not in p, (run, line)
                expected = lab.schema_format() if role == "reader" else (lab.schema_format(True) if condition == "structured" else lab.TEXT_FORMAT)
                assert p["response_format"] == expected, (run, line)
                pending = {"run": run, "effort": effort, "condition": condition, "role": role, "format": expected["type"]}
            elif e["event"] == "response":
                data = json.loads(e["raw_response"])
                assert data["model"].startswith(config["model"]) and data["provider"] == "OpenAI", (run, line)
                pending["elapsed_seconds"] = e["elapsed_seconds"]
                if pending["format"] == "json_schema":
                    schema["checked"] += 1
                    try:
                        lab.validate_object(json.loads(data["choices"][0]["message"]["content"]), nested=pending["role"] != "reader")
                    except (ValueError, TypeError, KeyError) as exc:
                        schema["invalid"].append({"run": run, "line": line, "error": str(exc)})
            elif e["event"] == "usage":
                u = e["usage"]
                calls.append({**pending, "prompt_tokens": u["prompt_tokens"], "completion_tokens": u["completion_tokens"],
                              "reasoning_tokens": u["completion_tokens_details"]["reasoning_tokens"],
                              "cost": Decimal(str(u["cost"])), "finish_reason": e["finish_reason"]})
            elif e["event"] in ("http_error", "transport_error"):
                errors[str(e.get("status", "transport"))] += 1
    for row in rows:
        events = episodes[(row["run"], row["scenario"])]
        result = [e["result"] for e in events if e["event"] == "episode_result"]
        assert len(result) == 1 and all(str(result[0][k]) == row[k] for k in row), (row["run"], row["scenario"])
        assert sum(e["event"] == "message" for e in events) == int(row["turns"]) <= lab.MAX_TURNS
        assert sum(e["event"] == "parse_result" and not e["ok"] for e in events) == int(row["format_errors"])
        assert sum(e["event"] == "request" for e in events) == int(row["http_requests"])
        if row["status"] != "crashed":
            assert sum(e["event"] == "reader_output" for e in events) == int(row["reader_calls"])
    return calls, schema, errors, episodes


def replay_first_eight(rows, episodes, scenarios):
    """Re-read the stored transcript with the original 8-message runner: same text, 8-message limit."""
    out = []
    for row in rows:
        if row["status"] == "crashed":
            continue
        replies = iter(e for e in episodes[(row["run"], row["scenario"])] if e["event"] in ("message", "reader_output"))
        def call(role, messages, fmt):
            reply = next(replies)
            assert role == reply.get("speaker", "reader")
            return reply["text"]
        sc = next(s for s in scenarios if str(s["id"]) == row["scenario"])
        result = {"deal_possible": int(row["deal_possible"])}
        english.negotiate(sc, row["condition"], call, lambda *a, **k: None, result)
        out.append({"run": row["run"], "effort": row["reasoning_effort"], "condition": row["condition"],
                    "scenario": row["scenario"], "outcome_30": row["outcome"], "turns_30": row["turns"],
                    "outcome_8": result["outcome"], "correct_8": result["correct"], "violation_8": result["violation"],
                    "turns_8": result["turns"], "format_errors_8": result["format_errors"], "reader_calls_8": result["reader_calls"]})
    return out


def call_summary(calls):
    out = []
    for effort in luna.EFFORTS:
        for role in ("buyer", "seller", "reader"):
            g = [c for c in calls if c["effort"] == effort and c["role"] == role]
            if not g:
                continue
            latency = sorted(c["elapsed_seconds"] for c in g)
            out.append({"effort": effort, "role": role, "calls": len(g),
                        "mean_prompt_tokens": round(mean(c["prompt_tokens"] for c in g), 1),
                        "mean_completion_tokens": round(mean(c["completion_tokens"] for c in g), 1),
                        "mean_reasoning_tokens": round(mean(c["reasoning_tokens"] for c in g), 1),
                        "max_reasoning_tokens": max(c["reasoning_tokens"] for c in g),
                        "median_latency_s": round(median(latency), 2),
                        "p90_latency_s": round(latency[int(0.9 * (len(latency) - 1))], 2),
                        "cost_usd": f"{sum(c['cost'] for c in g):.6f}",
                        "non_stop_finishes": sum(c["finish_reason"] != "stop" for c in g)})
    return out


def table(rows, cols, names):
    lines = ["| " + " | ".join(names) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(cols))) + "|"]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default=luna.SUITE)
    args = parser.parse_args()
    out = luna.HERE / "runs" / args.suite
    manifest = json.loads((out / "manifest.json").read_text())
    rows = read_rows(out / "results.csv")
    calls, schema, errors, episodes = audit(rows, manifest)
    replay = replay_first_eight(rows, episodes, manifest["scenarios"])
    def replayed(effort):
        return [{"condition": x["condition"], "outcome": x["outcome_8"], "correct": x["correct_8"],
                 "violation": x["violation_8"], "turns": x["turns_8"], "format_errors": x["format_errors_8"],
                 "reader_calls": x["reader_calls_8"]} for x in replay if x["effort"] == effort]
    deepseek8 = read_rows(luna.ROOT / "results.csv")
    outcomes = outcome_summary(deepseek8, "deepseek-v4.1-flash · reasoning off · 8")
    for e in luna.EFFORTS:
        outcomes += outcome_summary(replayed(e), f"gpt-6-luna · {e} · 첫 8턴 재판정")
    for e in luna.EFFORTS:
        outcomes += outcome_summary([r for r in rows if r["reasoning_effort"] == e], f"gpt-6-luna · {e} · 30")
    episode_cost = []
    for e in luna.EFFORTS:
        for c in lab.CONDITIONS:
            g = [r for r in rows if r["reasoning_effort"] == e and r["condition"] == c]
            episode_cost.append({"effort": e, "condition": c,
                                 "mean_reasoning_tokens": round(mean(int(r["reasoning_tokens"]) for r in g), 1),
                                 "mean_completion_tokens": round(mean(int(r["completion_tokens"]) for r in g), 1),
                                 "mean_elapsed_s": round(mean(float(r["elapsed_seconds"]) for r in g), 2),
                                 "cost_usd": f"{sum(Decimal(r['cost_usd']) for r in g):.6f}"})
    write_csv(out / "summary.csv", outcomes)
    write_csv(out / "calls.csv", call_summary(calls))
    write_csv(out / "episode-cost.csv", episode_cost)
    write_csv(out / "first-eight-replay.csv", replay)
    audit_report = {"episodes": len(rows), "requests": sum(int(r["http_requests"]) for r in rows),
                    "usage_records": len(calls), "http_errors": dict(errors),
                    "json_schema_responses_checked": schema["checked"], "json_schema_invalid": schema["invalid"],
                    "total_cost_usd": f"{sum(c['cost'] for c in calls):.6f}",
                    "ended_after_eighth_message": sum(x["outcome_8"] == "open" and x["outcome_30"] != "open" for x in replay)}
    (out / "audit.json").write_text(json.dumps(audit_report, ensure_ascii=False, indent=2) + "\n")
    cols = ["group", "condition", "episodes", "correct", "deal", "no_deal", "open", "crashed", "violations",
            "mean_turns", "max_turns", "format_errors", "reader_calls"]
    names = ["묶음", "조건", "에피소드", "정답", "deal", "no_deal", "open", "API 중단", "위반", "평균 턴", "최대 턴", "형식 오류", "reader 호출"]
    print(table(outcomes, cols, names))
    print()
    print(table(call_summary(calls), list(call_summary(calls)[0]), list(call_summary(calls)[0])))
    print()
    print(table(episode_cost, list(episode_cost[0]), list(episode_cost[0])))
    print(json.dumps(audit_report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
