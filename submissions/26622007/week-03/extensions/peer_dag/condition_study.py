"""Run three matched blocks of peer-DAG conditions and preserve every attempt."""
import argparse
import asyncio
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from audit import audit
from cli import ROOT, load_config
from core import fingerprint
from models import BASE, CONDITIONS, messages
from response_formats import response_format

BLOCKS = (CONDITIONS, CONDITIONS[1:] + CONDITIONS[:1], CONDITIONS[2:] + CONDITIONS[:2])
DEADLINE_SECONDS = 600


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def frozen_sources(protocol="PROTOCOL.md"):
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    files = [*ROOT.glob("*.py"), *(ROOT / name for name in ("case.json", "expected.json", "config.json")),
             ROOT / "conditions" / protocol, BASE / "contract_net.py", BASE / "openrouter_client.py"]
    hashes = {}
    for path in sorted(files):
        relative = path.relative_to(repo).as_posix()
        committed = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
        if committed != path.read_bytes():
            raise ValueError(f"commit {relative} before the study")
        hashes[relative] = fingerprint(path.read_text())
    return hashes


def canonical_facts(facts, expected):
    if facts is None:
        return None
    return {key: float(facts[key]) if type(facts.get(key)) in (int, float)
            else facts.get(key, {"missing": True}) for key in sorted(expected)}


def inspect_run(attempt, expected):
    run_id = attempt["run_id"]
    log = ROOT / "logs" / f"{run_id}.jsonl"
    rows, read_errors = [], []
    if log.exists():
        for number, line in enumerate(log.read_text().splitlines(), 1):
            try:
                rows.append(json.loads(line))
            except ValueError:
                read_errors.append(f"unreadable trace line {number}; original retained")
    start = next((row["settings"] for row in rows if row["event"] == "run_start"), None)
    result_path = ROOT / "runs" / run_id / "result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    trace = ({"passed": False, "errors": read_errors} if read_errors else
             audit(log) if log.exists() else {"passed": False, "errors": ["no trace"]})
    awards = [row for row in rows if row["event"] == "award"]
    proposals = [row for row in rows if row["event"] == "proposal"]
    c_proposals = [row["proposal"] for row in proposals if row["worker"] == "C"]
    phases = Counter(row["phase"] for row in rows if row["event"] == "call_start")
    reply_count = sum(row["event"] == "model_reply" and row["phase"] == "propose" for row in rows)
    artifact_path = ROOT / "runs" / run_id / "artifacts/release-review.json"
    outcome = json.loads(artifact_path.read_text()) if artifact_path.exists() else {}
    artifact = outcome.get("artifact")
    facts = artifact.get("facts") if artifact else None
    prompt_checks, format_checks = [], []
    for row in rows:
        if row["event"] == "http_request":
            actual = row["payload"]["messages"]
            payload = json.loads(actual[1]["content"])
            prompt_checks.append(actual == messages(row["contractor"], row["phase"], payload, attempt["condition"])
                                 and "personal_memory" not in payload and "handoff_memory" not in payload)
            format_checks.append(row["payload"].get("response_format") == response_format(row["phase"], payload))
    root_award = next((row["worker"] for row in awards if row["task_id"] == "release-review"), "")
    metric = {
        **attempt, "status": result.get("status", "timeout" if attempt["timed_out"] else "crashed"),
        "passed": bool(result.get("evaluation", {}).get("passed")),
        "checks_passed": sum(result.get("evaluation", {}).get("checks", {}).values()),
        "checks_total": len(expected), "trace_passed": trace["passed"],
        "calls": sum(phases.values()), "http_requests": result.get("http_requests"),
        "tasks": sum(row["event"] == "task_start" for row in rows),
        "max_depth": max((row["depth"] for row in rows if row["event"] == "task_start"), default=0),
        "contract_messages": phases["propose"] + reply_count + len(awards),
        "proposal_rejections": sum(row["event"] == "proposal_rejected" for row in rows),
        "valid_refusals": sum(not row["proposal"]["bid"] for row in proposals),
        "awards": dict(Counter(row["worker"] for row in awards)), "root_award": root_award,
        "c_proposals": len(c_proposals),
        "c_high_bids": sum(p["bid"] and p["confidence"] >= 95 for p in c_proposals),
        "reported_cost_usd": result.get("reported_cost_usd"),
        "cost_missing_responses": result.get("cost_missing_responses"),
        "elapsed_seconds": result.get("elapsed_seconds", attempt["wall_seconds"]),
        "peak_execution_calls": trace.get("peak_execution_calls", 0),
        "canonical_facts": canonical_facts(facts, expected),
        "prompt_checks": {"checked": len(prompt_checks), "passed": bool(prompt_checks) and all(prompt_checks)},
        "response_format_checks": {"checked": len(format_checks), "passed": bool(format_checks) and all(format_checks)},
        "error": result.get("error", "run did not produce result.json"),
        "providers": trace.get("providers", {}), "models": trace.get("models", {}),
        "trace_errors": trace.get("errors", []),
    }
    return metric, start


def aggregate(metrics):
    groups = {}
    for condition in CONDITIONS:
        subset = [item for item in metrics if item["condition"] == condition]
        awards = Counter()
        for item in subset:
            awards.update(item["awards"])
        vectors = [fingerprint(item["canonical_facts"]) for item in subset if item["canonical_facts"] is not None]
        costs = [item["reported_cost_usd"] for item in subset if item["reported_cost_usd"] is not None]
        groups[condition] = {
            "attempts": len(subset), "passed": sum(item["passed"] for item in subset),
            "checks_passed": sum(item["checks_passed"] for item in subset),
            "checks_total": sum(item["checks_total"] for item in subset),
            "mean_calls": sum(item["calls"] for item in subset) / len(subset),
            "mean_seconds": sum(item["elapsed_seconds"] for item in subset) / len(subset),
            "reported_cost_usd": sum(costs), "runs_with_cost": len(costs),
            "awards": dict(awards), "c_award_share": awards["C"] / sum(awards.values()) if awards else None,
            "c_root_awards": sum(item["root_award"] == "C" for item in subset),
            "depths": [item["max_depth"] for item in subset],
            "distinct_fact_vectors": len(set(vectors)), "runs_with_facts": len(vectors),
            "proposal_rejections": sum(item["proposal_rejections"] for item in subset),
            "c_high_bids": sum(item["c_high_bids"] for item in subset),
            "c_proposals": sum(item["c_proposals"] for item in subset),
        }
    return groups


def summarize(folder, manifest, attempts):
    expected = json.loads((ROOT / "expected.json").read_text())
    inspected = [inspect_run(attempt, expected) for attempt in attempts]
    metrics = [item[0] for item in inspected]
    settings = [item[1] for item in inspected]
    fields = ("requester", "limits", "transport", "case_sha", "expected_sha", "selection_policy", "sources", "base_sources",
              "concurrency_policy", "response_format_policy")
    controls = [fingerprint({key: setting.get(key) for key in fields}) for setting in settings if setting]
    integrity = {
        "all_nine_attempts_present": len(metrics) == 9,
        "three_per_condition": all(sum(m["condition"] == c for m in metrics) == 3 for c in CONDITIONS),
        "same_controls": len(controls) == 9 and len(set(controls)) == 1,
        "same_source_snapshot": frozen_sources(manifest.get("protocol", "PROTOCOL.md")) == manifest["source_hashes"],
        "recorded_conditions_match": all(setting and setting["condition"] == metric["condition"]
                                         for metric, setting in inspected),
        "all_recorded_prompts_match": all(metric["prompt_checks"]["passed"] for metric in metrics),
        "all_recorded_response_formats_match": all(metric["response_format_checks"]["passed"] for metric in metrics),
    }
    summary = {"manifest": manifest, "integrity": integrity, "conditions": aggregate(metrics), "runs": metrics}
    save(folder / "summary.json", summary)
    columns = ("run_id", "condition", "block", "status", "passed", "checks_passed", "checks_total", "calls",
               "http_requests", "tasks", "max_depth", "contract_messages", "root_award", "proposal_rejections",
               "valid_refusals", "c_proposals", "c_high_bids", "elapsed_seconds", "reported_cost_usd", "trace_passed")
    with (folder / "results.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(metrics)
    lines = ["# 재귀 Worker 구조의 세 조건 비교", "", "## 설정과 실험 범위", "",
             f"[사전 고정한 실험 규약](../{manifest.get('protocol', 'PROTOCOL.md')})에 따라 동일 출시 검토 사례를 조건별 3회 실행했다.",
             "모든 Worker가 계획·평가·실행·재위임·통합을 수행하며 max_depth=5, 장기 메모리 없음이다.",
             "기본 Contract Net의 배정 정확도 실험을 대체하지 않는 확장이다.", "",
             f"실행 소스 커밋: `{manifest['git_commit']}`. 모델: `{manifest['config']['transport']['model']}`, temperature=0.",
             f"실험 간 동시 실행 수: {manifest['parallel_runs_per_block']}. 실험 사이 대기: {manifest.get('inter_run_delay_seconds', 0)}초.",
             f"제공업체 설정: `{json.dumps(manifest['config']['transport'].get('provider', {}), ensure_ascii=False, sort_keys=True)}`.",
             "각 실험 내부 max_parallel=3은 유지한다. 시간은 공급자 지연과 부하의 영향을 받는다.", "",
             "## 결과", "", "|조건|전체 검사 통과|필드 검사|평균 호출|평균 초|보고 비용 합계|실제 최대 깊이|C 배정 비율|",
             "|---|---:|---:|---:|---:|---:|---|---:|"]
    for condition, group in summary["conditions"].items():
        share = f"{group['c_award_share']:.1%}" if group["c_award_share"] is not None else "해당 없음"
        lines.append(f"|{condition}|{group['passed']}/{group['attempts']}|{group['checks_passed']}/{group['checks_total']}|"
                     f"{group['mean_calls']:.1f}|{group['mean_seconds']:.1f}|${group['reported_cost_usd']:.5f}|{group['depths']}|{share}|")
    lines += ["", "## 반복 일치와 조작 확인", "", "|조건|산출물 있는 실행|서로 다른 핵심 결과|C의 95 이상 입찰/유효 제안|C 루트 선정|거절된 제안|",
              "|---|---:|---:|---:|---:|---:|"]
    for condition, group in summary["conditions"].items():
        lines.append(f"|{condition}|{group['runs_with_facts']}/3|{group['distinct_fact_vectors']}|"
                     f"{group['c_high_bids']}/{group['c_proposals']}|{group['c_root_awards']}/3|{group['proposal_rejections']}|")
    lines += ["", "핵심 결과 일치는 12개 지정 facts 값으로 계산하고 문장 표현은 비교하지 않는다. 같은 오답도 일치하므로 정확도와 함께 해석한다.",
              "산출물이 없는 실행은 일치 비교에서 제외하되 전체 검사 통과율의 분모 3에는 포함한다.",
              "필드 검사는 산출물이 없는 실행을 0/12로 센다. C 배정 비율은 루트를 포함한 모든 배정 중 C의 비율이며 오배정률이 아니다.", "",
              "## 실행별 증거", "", "|조건/회차|상태|검사|실제 깊이|원본 로그|산출물|", "|---|---|---:|---:|---|---|"]
    for metric in metrics:
        run_id = metric["run_id"]
        lines.append(f"|{metric['condition']}/{metric['block']}|{metric['status']}|{metric['checks_passed']}/12|{metric['max_depth']}|"
                     f"[trace](../../logs/{run_id}.jsonl)|[result](../../runs/{run_id}/result.json)|")
    lines += ["", "## 검증과 한계", "", *(f"- {key}: {value}" for key, value in integrity.items()), "",
              "- 입력은 한 종류의 합성 출시 검토 사례이며 조건별 3회뿐이다. 일반적인 우수성이나 완전한 결정성을 주장하지 않는다.",
              "- 하위 작업은 모델이 생성하므로 조건마다 작업 구성과 배정 기회가 달라질 수 있다. 하위 작업별 gold는 사후에 붙이지 않는다.",
              "- 선택 점수는 LLM 평가이며 동점에서는 자기 확신도를 사용한다. 높은 확신도가 정확한 능력 확률이라는 보장은 없다.",
              "- 초과 자신감 지시는 원래의 과장 금지 지시 뒤에 추가했다. 모델이 따랐는지는 실제 C의 유효 제안으로 측정한다.",
              "- 계약 메시지는 propose 호출 시작 + propose 응답 수 + award 수다. 평가·실행·통합 호출은 별도 LLM 호출 수에 포함한다.",
              "- 비용은 반환된 응답의 보고값 합계이며 응답이 없는 요청의 청구액은 알 수 없다. 시간 제한 종료도 실패로 보존한다.",
              "- 이 확장은 강의 기본 results.csv와 필수 9개 로그에 합산하지 않는다.", ""]
    (folder / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


async def attempt_run(folder, condition, block, run_id):
    console = folder / f"{run_id}.console.log"
    command = [sys.executable, "-u", str(ROOT / "cli.py"), "live", "--condition", condition, "--requester", "A", "--run-id", run_id]
    started, timed_out = time.monotonic(), False
    print(json.dumps({"event": "attempt_start", "condition": condition, "block": block, "run_id": run_id}), flush=True)
    with console.open("x", encoding="utf-8") as stream:
        stream.write("$ " + " ".join(command) + "\n")
        stream.flush()
        process = await asyncio.create_subprocess_exec(*command, stdout=stream, stderr=asyncio.subprocess.STDOUT)
        try:
            await asyncio.wait_for(process.wait(), DEADLINE_SECONDS)
        except asyncio.TimeoutError:
            timed_out = True
            process.terminate()
            await process.wait()
        except BaseException:
            if process.returncode is None:
                process.terminate()
            await process.wait()
            raise
        stream.write(f"\nexit_code={process.returncode}\ntimed_out={timed_out}\n")
    result = {"condition": condition, "block": block, "run_id": run_id, "exit_code": process.returncode,
              "timed_out": timed_out, "wall_seconds": round(time.monotonic() - started, 4)}
    save(folder / f"{run_id}.attempt.json", result)
    print(json.dumps({"event": "attempt_end", **result}), flush=True)
    return result


async def run_schedule(folder, schedule, *, serial=False, gap_seconds=0, source_hashes=None, protocol="PROTOCOL.md"):
    attempts = []
    for block in range(1, 4):
        if source_hashes is not None and frozen_sources(protocol) != source_hashes:
            raise ValueError("study source changed between blocks; previous attempts retained")
        items = [item for item in schedule if item["block"] == block]
        if serial:
            for item in items:
                if attempts and gap_seconds:
                    await asyncio.sleep(gap_seconds)
                attempts.append(await attempt_run(folder, item["condition"], block, item["run_id"]))
        else:
            attempts.extend(await asyncio.gather(*(attempt_run(folder, item["condition"], block, item["run_id"])
                                                  for item in items)))
    return attempts


async def main(serial=False, gap_seconds=0, protocol="PROTOCOL.md"):
    hashes = frozen_sources(protocol)
    config, _ = load_config()
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    folder = ROOT / "conditions" / batch_id
    folder.mkdir()
    schedule = [{"block": block, "condition": condition, "run_id": f"{batch_id}-{block}-{condition}"}
                for block, conditions in enumerate(BLOCKS, 1) for condition in conditions]
    manifest = {"batch_id": batch_id, "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_hashes": hashes, "config": config, "deadline_seconds": DEADLINE_SECONDS,
                "parallel_runs_per_block": 1 if serial else 3, "inter_run_delay_seconds": gap_seconds,
                "protocol": protocol, "schedule": schedule}
    save(folder / "manifest.json", manifest)
    print(json.dumps({"event": "study_start", "folder": str(folder), "schedule": schedule}), flush=True)
    attempts = await run_schedule(folder, schedule, serial=serial, gap_seconds=gap_seconds,
                                  source_hashes=hashes, protocol=protocol)
    summary = summarize(folder, manifest, attempts)
    print(json.dumps({"event": "study_end", "folder": str(folder), "integrity": summary["integrity"],
                      "conditions": summary["conditions"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", action="store_true", help="Run experiments sequentially; keep within-run parallelism")
    parser.add_argument("--gap-seconds", type=int, default=0)
    parser.add_argument("--protocol", choices=("PROTOCOL.md", "FINAL_PROTOCOL.md"), default="PROTOCOL.md")
    args = parser.parse_args()
    if not 0 <= args.gap_seconds <= 60 or (args.gap_seconds and not args.serial):
        parser.error("gap-seconds requires serial mode and must be 0..60")
    if args.protocol == "FINAL_PROTOCOL.md" and (not args.serial or args.gap_seconds != 15):
        parser.error("FINAL_PROTOCOL.md requires --serial --gap-seconds 15")
    asyncio.run(main(args.serial, args.gap_seconds, args.protocol))
