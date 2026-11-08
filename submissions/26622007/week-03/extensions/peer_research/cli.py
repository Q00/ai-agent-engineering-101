"""Run real web research and fresh-process memory follow-up experiments."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from common import BASE, PEER, ROOT, ConfigurationError, Limits, Outcome, Runtime, Task, fingerprint, read_key, redact
from research_audit import evaluate
from memory import MemoryContext, MemoryStore
from research_model import ResearchModel
from source_reader import SourceReader
from web_transport import WebTransport


def save(path, value, key=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(redact(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), key) + "\n", encoding="utf-8")


def committed_inputs(case):
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
    for path in (case, ROOT / "config.json", ROOT / "expected.json"):
        saved = subprocess.check_output(["git", "show", f"HEAD:{path.relative_to(repo).as_posix()}"], cwd=ROOT)
        if saved != path.read_bytes():
            raise ValueError(f"commit {path.name} before live execution")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def render(output, outcome, catalog, events, result):
    artifact = outcome.artifact
    lines = ["# 웹 조사 산출물", "", f"실행: `{result['run_id']}`", "",
             f"상태: {outcome.status} / 자동 계약 검사: {result['evaluation']['passed']}", ""]
    if artifact:
        lines += [artifact["summary"], "", "## 기록된 사실", ""]
        lines += [f"- **{k}**: {v}" for k,v in artifact["facts"].items()]
        lines += ["", "## 근거", ""] + [f"- {item}" for item in artifact["evidence"]]
    else:
        lines += [f"실패: {outcome.error}", "", "하위 JSON 산출물과 원본 로그를 함께 확인한다."]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = ["# 실제 검색 응답에서 반환된 출처", "", "인용이 반환되었다는 증거이며 모든 주장의 정확성을 보증하지 않는다.", ""]
    for source in sorted(catalog.values(), key=lambda s: s["url"]):
        lines += [f"- [{source['title'] or source['url']}]({source['url']}) — {source['retrieved_at']}"]
    (output / "sources.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = ["# 메모리 전달 기록", "", "|작업|단계|받은 Worker|본인 기록|요청자가 공유한 기록|", "|---|---|---|---|---|"]
    for e in events:
        if e["event"] == "memory_delivery":
            lines.append(f"|{e['task_id']}|{e['phase']}|{e['worker']}|{', '.join(e['personal_ids']) or '없음'}|{', '.join(e['handoff_ids']) or '없음'}|")
    (output / "memory-handoffs.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(args):
    config = json.loads((ROOT / "config.json").read_text())
    limits = Limits(**{k: config[k] for k in asdict(Limits())})
    case = ROOT / "cases" / f"{args.case}.json"
    task = Task.parse(json.loads(case.read_text()), root=True)
    sha = committed_inputs(case)
    env = BASE.parent / ".env"
    key = read_key(env if env.exists() else None)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + args.case + ("-no-memory" if args.no_memory else "") + "-" + uuid.uuid4().hex[:6]
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True)
    memory_dir = (ROOT / "state" / args.memory_set).resolve()
    if memory_dir.parent != (ROOT / "state").resolve():
        raise ValueError("memory-set must be a single directory name inside state")
    log_path = ROOT / "logs" / f"{run_id}.jsonl"
    log_path.parent.mkdir(exist_ok=True)
    events, catalog = [], {}
    started = time.monotonic()
    with log_path.open("x", encoding="utf-8") as stream:
        def emit(event, **fields):
            row = {"seq": len(events) + 1, "at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
            events.append(row)
            stream.write(redact(json.dumps(row, ensure_ascii=False, allow_nan=False), key) + "\n")
            stream.flush()
            if event in ("award", "task_end", "proposal_rejected"):
                print(json.dumps({k:v for k,v in row.items() if k in ("event", "task_id", "worker", "error")}, ensure_ascii=False), flush=True)

        memory = MemoryContext(MemoryStore(memory_dir), config["scope"], run_id, catalog, emit,
                               top_k=config["memory_top_k"], ttl_days=config["memory_ttl_days"], enabled=not args.no_memory)
        transport = WebTransport(key, config["transport"], config["search"], emit)
        reader = SourceReader(catalog, emit, config["source_fetch"])
        # Preserve this paused memory experiment's original per-worker execution policy.
        runtime = Runtime(ResearchModel(transport, catalog, reader), limits, emit, context=memory,
                          serialize_workers=True)
        settings = {"git_commit": sha, "case": args.case, "case_sha": fingerprint(asdict(task)),
                    "config": config, "requester": args.requester, "memory_enabled": memory.enabled,
                    "memory_snapshot_sha": fingerprint(memory.snapshot), "process_id": os.getpid(),
                    "sources": {str(p.relative_to(ROOT.parent)): fingerprint(p.read_text())
                                for folder in (ROOT, PEER) for p in sorted(folder.glob("*.py"))}}
        save(output / "settings.json", settings)
        save(output / "memory-before.json", memory.snapshot)
        emit("run_start", run_id=run_id, settings=settings, memory_counts=memory.counts())
        try:
            outcome = await runtime.run(task, args.requester)
        except asyncio.CancelledError:
            outcome = Outcome("cancelled", error="interrupted; partial results retained")
        memory.commit()
        # Only the offline evaluator reads the answer key, after all model calls have ended.
        expected = json.loads((ROOT / "expected.json").read_text())
        evaluation = evaluate(outcome, expected, memory, catalog, events, task, args.case == "02-revise")
        result = {"run_id": run_id, "status": outcome.status, "error": outcome.error,
                  "evaluation": evaluation, "calls": runtime.calls, "tasks": runtime.tasks,
                  "peak_calls": runtime.peak_calls, "elapsed_seconds": round(time.monotonic() - started, 3),
                  "http_requests": transport.http_requests, "source_http_requests": reader.requests,
                  "reported_search_requests": transport.search_requests,
                  "search_usage_missing_responses": transport.search_usage_missing,
                  "reported_cost_usd": transport.cost, "cost_missing_responses": transport.cost_missing,
                  "memory_enabled": memory.enabled, "memory_before": memory.counts(),
                  "memory_written": {w: sum(r["owner"] == w for r in memory.pending) for w in memory.snapshot},
                  "recalled_ids": sorted(memory.recalled), "shared_ids": sorted(memory.shared),
                  "delivered_ids": {w: sorted(ids) for w,ids in memory.delivered.items()},
                  "process_id": os.getpid(), "log": str(log_path), "output": str(output)}
        for path, item in runtime.outcomes.items():
            save((output / "artifacts").joinpath(*path.split("/")).with_suffix(".json"), asdict(item), key)
        save(output / "sources.json", catalog, key)
        save(output / "memory-written.json", memory.pending, key)
        save(output / "result.json", result, key)
        render(output, outcome, catalog, events, result)
        emit("run_end", result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if evaluation["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("01-investigate", "02-revise"), required=True)
    parser.add_argument("--requester", choices=("A", "B", "C"), default="A")
    parser.add_argument("--memory-set", default="rag-vector-adoption")
    parser.add_argument("--no-memory", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConfigurationError, ValueError, OSError) as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(2)
