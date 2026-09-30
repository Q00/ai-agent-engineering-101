"""Audit serialized live requests and generate the lab's factual results report."""
import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal
import hashlib
import json
from statistics import mean

import experiment as lab


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    args = parser.parse_args()
    out = lab.HERE / "runs" / args.suite
    manifest = json.loads((out / "manifest.json").read_text())
    for name, digest in manifest["inputs"].items():
        assert hashlib.sha256((lab.ROOT / name).read_bytes()).hexdigest() == digest, name
    with (lab.ROOT / "results.csv").open(newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["run"].startswith(args.suite + "-")]
    assert len(rows) == 36
    counts = Counter((r["condition"], r["scenario"]) for r in rows)
    assert all(counts[(c, str(s["id"]))] == 3 for c in lab.CONDITIONS for s in manifest["scenarios"])
    assert len({(r["run"], r["scenario"]) for r in rows}) == 36
    request_counts, provider_counts, format_counts, errors = Counter(), Counter(), Counter(), Counter()
    costs = defaultdict(lambda: Decimal(0))
    tokens = Counter()
    highlights = []
    schema_outputs, schema_invalid, total_responses = 0, [], 0
    all_events = {}
    for run in sorted({r["run"] for r in rows}):
        path = lab.ROOT / "logs" / f"{run}.jsonl"
        numbered = [(i, json.loads(line)) for i, line in enumerate(path.read_text().splitlines(), 1)]
        events = [e for _, e in numbered]
        all_events[run] = numbered
        condition = next(r["condition"] for r in rows if r["run"] == run)
        histories, transcript, scenario = None, None, None
        last_request = None
        for line, e in numbered:
            if e["event"] == "episode_start":
                scenario = next(s for s in manifest["scenarios"] if s["id"] == e["scenario"])
                histories, transcript = {"buyer": [], "seller": []}, []
                assert e["systems"] == {r: lab.system_prompt(r, scenario, condition) for r in histories}
            elif e["event"] == "request":
                p, role = e["payload"], e["contractor"]
                last_request = e
                for field in ("model", "temperature", "top_p", "reasoning", "provider"):
                    assert p[field] == manifest["config"][field], (run, line, field)
                expected = lab.schema_format() if role == "reader" else (lab.schema_format(True) if condition == "structured" else lab.TEXT_FORMAT)
                assert p["response_format"] == expected
                if role == "reader":
                    assert condition != "structured"
                    assert p["messages"] == [{"role": "system", "content": lab.READER_SYSTEM},
                        {"role": "user", "content": json.dumps(transcript, ensure_ascii=False)}]
                    if condition == "tagged":
                        assert transcript[-1]["text"].startswith("(propose)")
                else:
                    assert p["messages"] == [{"role": "system", "content": lab.system_prompt(role, scenario, condition)}] + histories[role]
                request_counts[condition] += 1
                format_counts[p["response_format"]["type"]] += 1
            elif e["event"] == "response":
                data = json.loads(e["raw_response"])
                total_responses += 1
                if "choices" in data:
                    assert data["model"] == manifest["config"]["model"]
                    assert data["provider"] == "DeepInfra"
                    provider_counts[data["provider"]] += 1
                    if last_request["payload"]["response_format"]["type"] == "json_schema":
                        schema_outputs += 1
                        try:
                            lab.validate_object(json.loads(data["choices"][0]["message"]["content"]),
                                nested=last_request["contractor"] != "reader")
                        except (ValueError, TypeError, KeyError) as exc:
                            schema_invalid.append({"run": run, "line": line, "error": str(exc)})
            elif e["event"] == "usage":
                costs[condition] += Decimal(str(e["usage"].get("cost", 0)))
                tokens[condition] += e["usage"].get("total_tokens", 0)
            elif e["event"] == "message":
                role = e["speaker"]; other = "seller" if role == "buyer" else "buyer"
                assert role == ("buyer" if e["turn"] % 2 else "seller")
                histories[role].append({"role": "assistant", "content": e["text"]})
                histories[other].append({"role": "user", "content": e["text"]})
                transcript.append({"speaker": role, "text": e["text"]})
                if len(highlights) < 30 and ("<eos>" in e["text"] or "what" in e["text"].lower() or "(reject-proposal)" in e["text"]):
                    highlights.append({"run": run, "line": line, "scenario": e["scenario"], "turn": e["turn"], "text": e["text"]})
            elif e["event"] in ("http_error", "transport_error"):
                errors[str(e.get("status", "transport"))] += 1
        for row in [r for r in rows if r["run"] == run]:
            ep = [e for e in events if str(e["scenario"]) == row["scenario"]]
            starts = [e for e in ep if e["event"] == "episode_start"]
            assert len(starts) == 1
            assert sum(e["event"] == "message" for e in ep) == int(row["turns"])
            assert sum(e["event"] == "parse_result" and not e["ok"] for e in ep) == int(row["format_errors"])
            assert sum(e["event"] == "episode_result" for e in ep) == 1
            assert int(row["turns"]) <= 8
            if row["outcome"]:
                assert sum(e["event"] == "reader_output" for e in ep) == int(row["reader_calls"])
                if condition == "free": assert int(row["reader_calls"]) == int(row["turns"])
                if condition == "structured": assert int(row["reader_calls"]) == 0
                sc = next(s for s in manifest["scenarios"] if str(s["id"]) == row["scenario"])
                violation = int(row["outcome"] == "deal" and not sc["reserve"] <= int(row["price"]) <= sc["budget"])
                correct = int((row["outcome"] == "deal" and not violation) or (row["outcome"] == "no_deal" and sc["reserve"] > sc["budget"]))
                assert (int(row["violation"]), int(row["correct"])) == (violation, correct)
    summary = {}
    for c in lab.CONDITIONS:
        group = [r for r in rows if r["condition"] == c]
        summary[c] = {"episodes": len(group), "correct": sum(int(r["correct"] or 0) for r in group),
            **{outcome: sum(r["outcome"] == outcome for r in group) for outcome in ("deal", "no_deal", "open")},
            "crashed": sum(not r["outcome"] for r in group),
            "violations": sum(int(r["violation"] or 0) for r in group),
            "mean_turns": round(mean(int(r["turns"]) for r in group), 3),
            "format_errors": sum(int(r["format_errors"]) for r in group),
            "reader_calls": sum(int(r["reader_calls"]) for r in group),
            "http_requests": request_counts[c], "tokens": tokens[c], "cost_usd": str(costs[c])}
    audit = {"suite": args.suite, "episodes": len(rows), "runs": len(all_events), "summary": summary,
        "all_request_settings_and_formats_verified": True, "all_histories_verified": True,
        "response_formats": dict(format_counts), "providers": dict(provider_counts),
        "responses": total_responses, "schema_outputs": schema_outputs, "schema_invalid": schema_invalid,
        "http_errors": dict(errors), "highlights": highlights}
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Week 04 HTML 실습 실행 기록", "", "이 문서는 실제 실행 결과와 검증 기록이다. FIPA 비교와 제출용 최종 해석은 별도 작성 대상이다.", "",
        f"실험 `{args.suite}`, 사전 소스 커밋 `{manifest['source_commit']}`.",
        "DeepSeek V4.1 Flash / DeepInfra FP8, temperature=1.0, top_p=0.95, reasoning off, MAX_TURNS=8.",
        "자전거·탁상등·교재·키보드 네 시나리오, 세 조건, 조건별 세 번 반복: 총 36개 에피소드.", "",
        "## 조건별 결과", "", "| 조건 | 정답/12 | deal | no_deal | open | 중단 | 위반 | 평균 턴 | 형식 오류 | reader 호출 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c, s in summary.items():
        lines.append(f"| {c} | {s['correct']} | {s['deal']} | {s['no_deal']} | {s['open']} | {s['crashed']} | {s['violations']} | {s['mean_turns']} | {s['format_errors']} | {s['reader_calls']} |")
    lines += ["", "## 재현과 검증", "", "[실행 명령과 HTML 대응](lab/README.md), [공식 코드 조각](lab/reference/lecture-code.txt).",
        f"직렬화 요청 {sum(request_counts.values())}개에서 모델·온도·top_p·reasoning·제공업체·response_format과 전체 대화 이력을 검사했다.",
        f"HTTP/전송 오류 기록: `{dict(errors)}`. API usage.cost 합계 USD `{sum(costs.values())}`.",
        f"구조화 응답 {schema_outputs}개 중 로컬 형식 검증 실패 {len(schema_invalid)}개. 형식 검증은 의미 판정의 정확성을 보장하지 않는다.",
        "HTML은 완성된 starter가 아니며 일부 문구와 구현이 생략돼 있다. 공개 코드 문구와 참조 사례를 그대로 사용하고 초기화·파서·기록·재개를 구현했다.",
        "사용자의 기존 규칙으로 reader 및 structured 요청에는 strict JSON Schema를 적용했다. 이 API 제약은 HTML의 Claude CLI 참조 실행에 명시되지 않아 형식 오류율을 직접 비교할 수 없다.",
        "3개 독립 run을 동시에 진행하되 에피소드 내부의 발언 순서와 대화 이력은 분리했다. 이전 pilot 결과는 이 표에 포함하지 않는다.",
        "", "## 에피소드 전체", "", "| run | 조건 | 시나리오 | 가능 | 결과 | 가격 | 정답 | 위반 | 턴 | 형식 오류 | reader | note |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda r: (r["condition"], r["run"], int(r["scenario"]))):
        lines.append("| " + " | ".join(r[k].replace("|", "\\|") for k in lab.HEADER) + " |")
    lines += ["", "각 run의 원문과 모든 판독은 `logs/<run>.txt`, API 원본은 `logs/<run>.jsonl`에 있다.",
              f"HTML 참조 사례와 대조한 [실제 관찰](lab/runs/{args.suite}/OBSERVATIONS.md).",
              f"요청/이력 검증 및 상세 집계: [audit.json](lab/runs/{args.suite}/audit.json)."]
    (lab.ROOT / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"summary": summary, "http_errors": dict(errors), "requests": sum(request_counts.values()),
                      "schema_invalid": schema_invalid}, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
