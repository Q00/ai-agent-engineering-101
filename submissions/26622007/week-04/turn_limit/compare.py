"""Audit both suites and make independent-sample and same-transcript tables."""
import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal
import hashlib
import json
from statistics import mean

import run_unlimited as ext

lab = ext.lab


def read_rows(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def aggregate(rows, mode):
    result = []
    for c in lab.CONDITIONS:
        group = [r for r in rows if r["condition"] == c]
        result.append({"mode": mode, "condition": c, "episodes": len(group),
            "correct": sum(int(r["correct"] or 0) for r in group),
            **{x: sum(r["outcome"] == x for r in group) for x in ("deal", "no_deal", "open")},
            "censored": sum(r.get("status") == "censored" for r in group),
            "crashed": sum(r.get("status") == "crashed" for r in group),
            "violations": sum(int(r["violation"] or 0) for r in group),
            "mean_turns": round(mean(int(r["turns"]) for r in group), 2),
            "max_turns": max(int(r["turns"]) for r in group),
            "format_errors": sum(int(r["format_errors"]) for r in group),
            "reader_calls": sum(int(r["reader_calls"]) for r in group)})
    return result


def write_csv(path, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader(); w.writerows(rows)


def audit(rows, manifest, unlimited):
    assert len(rows) == 36
    assert len({(r["run"], r["scenario"]) for r in rows}) == 36
    counts = Counter((r["condition"], r["scenario"]) for r in rows)
    assert all(counts[(c, str(s["id"]))] == 3 for c in lab.CONDITIONS for s in manifest["scenarios"])
    for path, digest in manifest["inputs"].items():
        assert hashlib.sha256((ext.ROOT / path).read_bytes()).hexdigest() == digest, path
    settings = ("model", "temperature", "top_p", "reasoning", "provider")
    requests, responses, errors, formats = Counter(), Counter(), Counter(), Counter()
    costs = defaultdict(lambda: Decimal(0))
    schema_count, schema_errors = 0, []
    events_by_episode = {}
    for run in sorted({r["run"] for r in rows}):
        condition = next(r["condition"] for r in rows if r["run"] == run)
        numbered = [(i, json.loads(line)) for i, line in enumerate((ext.ROOT / "logs" / f"{run}.jsonl").read_text().splitlines(), 1)]
        histories, transcript, scenario, last_request = None, None, None, None
        for line, e in numbered:
            if e["scenario"] is not None:
                events_by_episode.setdefault((run, str(e["scenario"])), []).append((line, e))
            if e["event"] == "episode_start":
                scenario = next(s for s in manifest["scenarios"] if s["id"] == e["scenario"])
                histories, transcript = {"buyer": [], "seller": []}, []
                assert e["systems"] == {r: lab.system_prompt(r, scenario, condition) for r in histories}
            elif e["event"] == "request":
                last_request = e
                p, role = e["payload"], e["contractor"]
                for field in settings: assert p[field] == manifest["config"][field], (run, line, field)
                expected = lab.schema_format() if role == "reader" else (lab.schema_format(True) if condition == "structured" else lab.TEXT_FORMAT)
                assert p["response_format"] == expected
                if role == "reader":
                    assert condition != "structured"
                    assert p["messages"] == [{"role": "system", "content": lab.READER_SYSTEM},
                        {"role": "user", "content": json.dumps(transcript, ensure_ascii=False)}]
                    if condition == "tagged": assert transcript[-1]["text"].startswith("(propose)")
                else:
                    assert p["messages"] == [{"role": "system", "content": lab.system_prompt(role, scenario, condition)}] + histories[role]
                requests[condition] += 1
                formats[p["response_format"]["type"]] += 1
            elif e["event"] == "response":
                data = json.loads(e["raw_response"])
                assert data["model"] == manifest["config"]["model"]
                assert data["provider"] == "DeepInfra"
                responses[condition] += 1
                if last_request["payload"]["response_format"]["type"] == "json_schema":
                    schema_count += 1
                    try: lab.validate_object(json.loads(data["choices"][0]["message"]["content"]), nested=last_request["contractor"] != "reader")
                    except (ValueError, TypeError, KeyError) as exc:
                        schema_errors.append({"run": run, "line": line, "error": str(exc)})
            elif e["event"] == "message":
                role = e["speaker"]; other = "seller" if role == "buyer" else "buyer"
                assert role == ("buyer" if e["turn"] % 2 else "seller")
                histories[role].append({"role": "assistant", "content": e["text"]})
                histories[other].append({"role": "user", "content": e["text"]})
                transcript.append({"speaker": role, "text": e["text"]})
            elif e["event"] == "usage": costs[condition] += Decimal(str(e["usage"].get("cost", 0)))
            elif e["event"] in ("http_error", "transport_error"): errors[str(e.get("status", "transport"))] += 1
    prefix_rows, later = [], []
    for row in rows:
        ep = events_by_episode[(row["run"], row["scenario"])]
        events = [e for _, e in ep]
        assert sum(e["event"] == "episode_start" for e in events) == 1
        result_event = [e["result"] for e in events if e["event"] == "episode_result"]
        assert len(result_event) == 1
        for k in row: assert str(result_event[0][k]) == row[k], (row["run"], row["scenario"], k)
        assert sum(e["event"] == "message" for e in events) == int(row["turns"])
        assert sum(e["event"] == "parse_result" and not e["ok"] for e in events) == int(row["format_errors"])
        if row.get("status") != "crashed":
            assert sum(e["event"] == "reader_output" for e in events) == int(row["reader_calls"])
        sc = next(s for s in manifest["scenarios"] if str(s["id"]) == row["scenario"])
        assert int(row["deal_possible"]) == int(sc["reserve"] <= sc["budget"])
        if not unlimited:
            assert int(row["turns"]) <= 8
            continue
        if row["status"] == "censored":
            assert all(row[k] == "" for k in ("outcome", "price", "correct", "violation"))
            assert float(row["elapsed_seconds"]) >= manifest["observation_seconds"]
        if row["status"] == "completed":
            assert row["outcome"] in ("deal", "no_deal")
        if row["status"] != "crashed":
            # Replay every completed model/reader output through the same protocol.
            replies = [e for e in events if e["event"] in ("message", "reader_output")]
            cursor = [0]
            def call(role, messages, fmt):
                reply = replies[cursor[0]]; cursor[0] += 1
                assert role == reply.get("speaker", "reader")
                return reply["text"]
            def clock(): return manifest["observation_seconds"] if cursor[0] == len(replies) else 0
            replay = {"deal_possible": int(row["deal_possible"])}
            ext.negotiate(sc, row["condition"], call, lambda *a, **k: None, replay,
                          manifest["observation_seconds"], clock=clock)
            for k in ("outcome", "price", "correct", "violation", "turns", "format_errors", "reader_calls", "status"):
                assert str(replay[k]) == row[k], (row["run"], row["scenario"], k)
        if sum(e["event"] == "parse_result" for e in events) >= 8 or row["status"] == "completed":
            replies = iter(e for e in events if e["event"] in ("message", "reader_output"))
            def call_prefix(role, messages, fmt):
                reply = next(replies)
                assert role == reply.get("speaker", "reader")
                return reply["text"]
            prefix = {"run": row["run"], "condition": row["condition"], "scenario": row["scenario"],
                      "deal_possible": int(row["deal_possible"])}
            lab.negotiate(sc, row["condition"], call_prefix, lambda *a, **k: None, prefix)
        else:
            prefix = {k: row[k] for k in lab.HEADER if k != "note"}
            prefix["status"] = row["status"]
        prefix_rows.append({k: prefix.get(k, "") for k in lab.HEADER + ["status"]})
        if int(row["turns"]) > 8:
            later.append({"run": row["run"], "condition": row["condition"], "scenario": row["scenario"],
                          "turns": int(row["turns"]), "outcome": row["outcome"], "status": row["status"],
                          "correct": row["correct"], "log_last_message_line": next(line for line, e in reversed(ep) if e["event"] == "message")})
    return {"requests": dict(requests), "responses": dict(responses), "http_errors": dict(errors),
            "response_formats": dict(formats), "schema_outputs": schema_count, "schema_errors": schema_errors,
            "cost_usd": {c: str(v) for c, v in costs.items()},
            "source_hashes_verified": True, "request_settings_and_histories_verified": True,
            "replayed_outcomes_verified": unlimited, "beyond_eight": later}, prefix_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="unlimited-deepseek-20260922")
    args = parser.parse_args()
    out = ext.HERE / "runs" / args.suite
    manifest = json.loads((out / "manifest.json").read_text())
    baseline_manifest = json.loads((ext.ROOT / "lab/runs/html-deepseek-20260922/manifest.json").read_text())
    for k in ("scenarios", "format", "roles", "common", "reader_system", "repeats", "jobs"):
        assert manifest[k] == baseline_manifest[k], k
    baseline_config = dict(baseline_manifest["config"]); baseline_config["max_http_requests"] = None
    assert manifest["config"] == baseline_config
    assert manifest["max_turns"] is None
    baseline = read_rows(ext.ROOT / "results.csv")
    unlimited = read_rows(out / "results.csv")
    base_audit, _ = audit(baseline, baseline_manifest, False)
    unlimited_audit, prefixes = audit(unlimited, manifest, True)
    report_audit = {"baseline": base_audit, "unlimited": unlimited_audit}
    (out / "audit.json").write_text(json.dumps(report_audit, ensure_ascii=False, indent=2) + "\n")
    summaries = aggregate(baseline, "8-turn") + aggregate(unlimited, "no-turn-limit")
    prefix_summaries = aggregate(prefixes, "same-transcript-first-eight")
    write_csv(out / "summary.csv", summaries)
    write_csv(out / "first-eight-replay.csv", prefixes)
    lines = ["# 8턴 제한과 턴 제한 없는 협상 비교", "",
        "동일한 DeepSeek V4.1 Flash / DeepInfra FP8, temperature=1.0, top_p=0.95, reasoning off.",
        "각 묶음은 세 조건 × 네 시나리오 × 세 반복 = 36개 에피소드다.",
        "8턴 제한은 기존 실제 실행, 무제한은 새로 실행한 독립 표본이다.",
        "턴 무제한은 에피소드당 180초를 관측하고 다음 발언 전에 시간을 확인한다. 진행 중 발언과 reader는 완료한다.",
        "`관측 중단`은 아직 종료하지 않았다는 뜻이며 `no_deal` 또는 `open`으로 판정하지 않는다.",
        "`확인 정답/12`는 전체 시도 중 확인된 정답 수이며 관측 중단의 최종 성패는 미상이다.",
        "평균/최대 턴은 관측된 발언 수이며 중단된 협상의 최종 길이가 아니다.", ""]
    for label, group in (("8턴 제한", summaries[:3]), ("턴 제한 없음 · 180초 관측", summaries[3:])):
        lines += [f"## {label}", "", "| 조건 | 확인 정답/12 | deal | no_deal | open | 관측 중단 | API 중단 | 위반 | 평균 턴 | 최대 턴 | 형식 오류 | reader 호출 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in group:
            lines.append("| " + " | ".join(str(r[k]) for k in ("condition", "correct", "deal", "no_deal", "open", "censored", "crashed", "violations", "mean_turns", "max_turns", "format_errors", "reader_calls")) + " |")
        lines += [""]
    lines += ["## 같은 대화의 8턴 이후 변화", "",
              "무제한 실행의 첫 8턴을 원래 8턴 runner로 재생했다. 프롬프트에 제한이 없으므로 같은 대화의 prefix 비교다.",
              "이 표는 별도 API 재실행이 아닌 저장된 실제 응답의 재판정이다.", "",
              "| 조건 | 첫 8턴 정답/12 | 계속 관측 후 정답/12 | 8턴 이후 종료 | 8턴 이후 정답 종료 | 계속 관측했지만 미종료 |",
              "|---|---:|---:|---:|---:|---:|"]
    for p, u in zip(prefix_summaries, summaries[3:]):
        after = [r for r in unlimited_audit["beyond_eight"] if r["condition"] == p["condition"]]
        lines.append(f"| {p['condition']} | {p['correct']} | {u['correct']} | {sum(r['status'] == 'completed' for r in after)} | {sum(r['correct'] == '1' for r in after)} | {u['censored']} |")
    lines += ["", "## 8턴을 넘긴 에피소드", "", "| run | 시나리오 | 관측 턴 | 결과 | 상태 | 정답 | 원문 |", "|---|---:|---:|---|---|---:|---|"]
    for r in unlimited_audit["beyond_eight"]:
        lines.append(f"| {r['run']} | {r['scenario']} | {r['turns']} | {r['outcome']} | {r['status']} | {r['correct']} | [로그](../logs/{r['run']}.txt) |")
    lines += ["", "## 실행 근거", "",
        "기존 8턴 [실행 보고서](../REPORT.md), [무제한 실행 방법](README.md), [원문 사례 분석](OBSERVATIONS.md).",
        f"[전체 결과 CSV](runs/{args.suite}/results.csv), [집계 CSV](runs/{args.suite}/summary.csv), [첫 8턴 재판정](runs/{args.suite}/first-eight-replay.csv), [요청 감사](runs/{args.suite}/audit.json).",
        f"무제한 실제 HTTP 요청 {sum(unlimited_audit['requests'].values())}개; HTTP/전송 오류 {unlimited_audit['http_errors']}; usage.cost 합계 USD {sum(Decimal(x) for x in unlimited_audit['cost_usd'].values())}.",
        "모든 요청의 설정·response_format·전체 이력을 검증하고 저장된 응답을 재생해 최종 판정과 일치함을 확인했다.",
        "두 독립 표본에는 샘플링 변동이 있다. 같은 대화의 첫 8턴 비교는 그 영향을 줄이지만 이 소표본으로 일반적인 우열을 단정하지 않는다.",
        "원래 실습의 strict JSON Schema 적용 차이는 유지했으며, 이번 비교에서는 두 실험의 API 형식 제약이 같다.",
        "", "## 무제한 실행 전체 결과", "", "| run | 조건 | 시나리오 | 결과 | 상태 | 가격 | 정답 | 위반 | 턴 | 형식 오류 | reader | 관측 초 |", "|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(unlimited, key=lambda r: (r["condition"], r["run"], int(r["scenario"]))):
        lines.append("| " + " | ".join(r[k] for k in ("run", "condition", "scenario", "outcome", "status", "price", "correct", "violation", "turns", "format_errors", "reader_calls", "elapsed_seconds")) + " |")
    (ext.HERE / "COMPARISON.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"summary": summaries, "prefix_summary": prefix_summaries,
                      "requests": unlimited_audit["requests"], "errors": unlimited_audit["http_errors"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
