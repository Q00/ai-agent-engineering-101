"""CLI with explicit model evaluation, offline replay and separate publishing."""

import argparse
import sys
from pathlib import Path

from .batch import run_batch
from .benchmark import acceptance, run_benchmark
from .comments import publish
from .grading import weighted_score
from .itemwise import judge_items, refresh_policies, replay_items, resolve_policies
from .ledger import QuestionBox
from .policy import apply_policy, interview
from .providers import judge
from .review import ROOT, finalize, prepare, validate_review
from .snapshot import collect_local, collect_pr, verify_snapshot
from .storage import canonical, read_json, write_new


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="AX assignment evidence-based review harness"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser(
        "interview", help="Save optional bonus rules for a cohort"
    )
    setup.add_argument("--out", type=Path, required=True)
    batch = commands.add_parser(
        "batch", help="Process an explicit PR list with bounded itemwise concurrency"
    )
    batch.add_argument("--prs", type=int, nargs="+", required=True)
    batch.add_argument("--repo", default="Q00/ai-agent-engineering-101")
    batch.add_argument("--week", default="week-05")
    batch.add_argument("--spec-ref", required=True, help="Full assignment commit SHA")
    batch.add_argument("--rubric", type=Path, default=ROOT / "rubrics/week05.json")
    batch.add_argument("--policy", type=Path, required=True)
    batch.add_argument("--config", type=Path)
    batch.add_argument(
        "--stage", choices=["prepare", "evaluate", "finalize"], default="prepare"
    )
    batch.add_argument("--out", type=Path, required=True)
    batch.add_argument("--resume", action="store_true")
    batch.add_argument("--retry-failed", action="store_true")
    batch.add_argument("--max-chars", type=int, default=180_000)
    batch.add_argument("--per-file-chars", type=int, default=12_000)
    batch.add_argument("--itemwise", action="store_true", default=True)
    batch.add_argument("--legacy-review", dest="itemwise", action="store_false")
    batch.add_argument("--ledger", type=Path)
    batch.add_argument("--workers", type=int, default=4)
    batch.add_argument("--provider-retries", type=int, choices=[0, 1], default=0)
    batch.add_argument("--max-task-chars", type=int, default=400_000)
    local = commands.add_parser(
        "collect-local", help="Snapshot local files without executing them"
    )
    local.add_argument("source", type=Path)
    local.add_argument("--spec", type=Path, required=True)
    local.add_argument("--out", type=Path, required=True)
    local.add_argument("--week", default="week-05")
    remote = commands.add_parser("collect-pr", help="Read an assignment PR from GitHub")
    remote.add_argument("number", type=int)
    remote.add_argument("--repo", default="Q00/ai-agent-engineering-101")
    remote.add_argument("--spec-ref", default="main")
    remote.add_argument("--out", type=Path, required=True)
    remote.add_argument("--week", default="week-05")
    remote.add_argument(
        "--head-sha", help="Historical commit on the PR's current ancestry"
    )
    prep = commands.add_parser(
        "prepare",
        help="Run deterministic checks and create review packet; no model call",
    )
    prep.add_argument("snapshot", type=Path)
    prep.add_argument("--rubric", type=Path, default=ROOT / "rubrics/week05.json")
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--max-chars", type=int, default=180_000)
    prep.add_argument("--per-file-chars", type=int, default=12_000)
    evidence_mode = prep.add_mutually_exclusive_group()
    evidence_mode.add_argument("--full-files", action="store_true", default=True)
    evidence_mode.add_argument("--excerpt", dest="full_files", action="store_false")
    prep.add_argument("--ledger", type=Path, help="Apply saved instructor answers")
    prep.add_argument(
        "--policy", type=Path, help="Saved interview policy; otherwise interview now"
    )
    call = commands.add_parser(
        "judge",
        help="Call a model explicitly; may send evidence externally and incur cost",
    )
    call.add_argument("run", type=Path)
    call.add_argument("--config", type=Path, required=True)
    call.add_argument("--out", type=Path, required=True)
    call.add_argument("--ledger", type=Path)
    call.add_argument("--workers", type=int, default=4)
    call.add_argument("--max-task-chars", type=int, default=400000)
    call.add_argument("--retry-failed", action="store_true")
    call.add_argument("--provider-retries", type=int, choices=[0, 1], default=0)
    call.add_argument("--legacy-review", action="store_true")
    units = commands.add_parser("judge-items", help="Parallel full-file item judgments")
    units.add_argument("run", type=Path)
    units.add_argument("--config", type=Path, required=True)
    units.add_argument("--out", type=Path, required=True)
    units.add_argument("--ledger", type=Path, required=True)
    units.add_argument("--workers", type=int, default=4)
    units.add_argument("--max-task-chars", type=int, default=400_000)
    units.add_argument("--retry-failed", action="store_true")
    units.add_argument("--provider-retries", type=int, choices=[0, 1], default=0)
    replay = commands.add_parser(
        "replay-items", help="Reassemble saved judgments offline"
    )
    replay.add_argument("packet", type=Path)
    replay.add_argument("run", type=Path)
    replay.add_argument("--ledger", type=Path, required=True)
    replay.add_argument("--out", type=Path, required=True)
    replay.add_argument("--benchmark-only", action="store_true")
    replay.add_argument("--repair-bonus-ids", action="store_true")
    questions = commands.add_parser(
        "questions", help="List instructor policy questions"
    )
    questions.add_argument("--ledger", type=Path, required=True)
    answer = commands.add_parser("answer-policy", help="Record an instructor answer")
    answer.add_argument("question")
    answer.add_argument("value")
    answer.add_argument("--reviewer", required=True)
    answer.add_argument("--ledger", type=Path, required=True)
    link = commands.add_parser(
        "link-questions", help="Instructor declares policy questions equivalent"
    )
    link.add_argument("questions", nargs="+")
    link.add_argument("--reviewer", required=True)
    link.add_argument("--ledger", type=Path, required=True)
    refresh = commands.add_parser(
        "refresh-policy",
        help="Apply answers to explicit packets; optionally evaluate affected tasks",
    )
    refresh.add_argument("--packets", type=Path, nargs="+", required=True)
    refresh.add_argument("--ledger", type=Path, required=True)
    refresh.add_argument("--out", type=Path, required=True)
    refresh.add_argument(
        "--config",
        type=Path,
        help="Explicitly call model where cached judgments no longer apply",
    )
    refresh.add_argument("--workers", type=int, default=4)
    refresh.add_argument("--max-task-chars", type=int, default=400000)
    rescore = commands.add_parser(
        "rescore", help="Apply explicit group weights offline"
    )
    rescore.add_argument("run", type=Path)
    rescore.add_argument("--policy", type=Path, required=True)
    rescore.add_argument("--out", type=Path, required=True)
    bench = commands.add_parser(
        "benchmark", help="Run real-model reproducibility tests"
    )
    bench.add_argument("--snapshots", type=Path, nargs="+", required=True)
    bench.add_argument("--rubric", type=Path, required=True)
    bench.add_argument("--policy", type=Path, required=True)
    bench.add_argument("--config", type=Path, required=True)
    bench.add_argument("--out", type=Path, required=True)
    bench.add_argument("--workers", type=int, default=4)
    bench.add_argument("--max-task-chars", type=int, default=400_000)
    bench.add_argument("--no-variants", action="store_true")
    bench.add_argument("--provider-retries", type=int, choices=[0, 1], default=0)
    human = commands.add_parser(
        "import-review", help="Validate a manually completed response template"
    )
    human.add_argument("run", type=Path)
    human.add_argument("review", type=Path)
    human.add_argument("--reviewer", required=True)
    human.add_argument("--kind", choices=["human", "current-session"], default="human")
    human.add_argument("--out", type=Path, required=True)
    final = commands.add_parser(
        "finalize", help="Validate saved review and calculate draft points offline"
    )
    final.add_argument("run", type=Path)
    final.add_argument("response", type=Path)
    final.add_argument("--out", type=Path, required=True)
    posting = commands.add_parser(
        "publish", help="Preview a PR comment; --write explicitly posts it"
    )
    posting.add_argument("run", type=Path)
    posting.add_argument("--write", action="store_true")
    evidence = commands.add_parser(
        "evidence", help="Read exact source lines from a snapshot"
    )
    evidence.add_argument("snapshot", type=Path)
    evidence.add_argument("path")
    evidence.add_argument("--start", type=int, default=1)
    evidence.add_argument("--end", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "interview":
            write_new(args.out, interview())
            print(f"Saved bonus policy: {args.out}")
        elif args.command == "batch":
            itemwise = None
            if args.itemwise:
                if args.ledger is None:
                    args.ledger = args.out / "judgments"
                itemwise = dict(
                    ledger=str(args.ledger.resolve()),
                    workers=args.workers,
                    max_task_chars=args.max_task_chars,
                    provider_retries=args.provider_retries,
                )
            summary = run_batch(
                args.repo,
                args.prs,
                args.week,
                args.spec_ref,
                read_json(args.rubric),
                read_json(args.policy),
                args.out,
                config=read_json(args.config) if args.config else None,
                stage=args.stage,
                resume=args.resume,
                retry_failed=args.retry_failed,
                max_chars=args.max_chars,
                per_file=args.per_file_chars,
                itemwise=itemwise,
            )
            print(canonical(summary["counts"]))
            print(f"Summary: {args.out / 'summary.md'}")
            unavailable = any(i.get("provider_unavailable") for i in summary["items"])
            return 1 if summary["counts"].get("failed") or unavailable else 0
        elif args.command == "publish":
            print(canonical(publish(args.run, write=args.write)))
        elif args.command == "collect-local":
            result = collect_local(args.source, args.spec, args.out, week=args.week)
            print(f"snapshot {result['id']}")
        elif args.command == "collect-pr":
            if args.number <= 0:
                raise ValueError("PR number must be positive")
            result = collect_pr(
                args.repo,
                args.number,
                args.out,
                args.spec_ref,
                week=args.week,
                head_sha=args.head_sha,
            )
            print(f"snapshot {result['id']} ({len(result['files'])} files)")
        elif args.command == "prepare":
            if args.max_chars <= 0 or args.per_file_chars <= 0:
                raise ValueError("Evidence limits must be positive")
            snapshot = read_json(args.snapshot)
            rubric = apply_policy(
                read_json(args.rubric),
                read_json(args.policy) if args.policy else interview(),
            )
            if args.ledger:
                rubric = resolve_policies(rubric, args.ledger)
            if args.full_files:
                sizes = [len(f["text"] or "") for f in snapshot["files"].values()]
                args.max_chars, args.per_file_chars = (
                    sum(n + 1 for n in sizes),
                    max(sizes, default=0) + 1,
                )
            bundle = prepare(
                snapshot,
                rubric,
                args.out,
                args.max_chars,
                args.per_file_chars,
            )
            print(f"prepared {bundle['id']}; no model called")
        elif args.command == "judge-items":
            response = judge_items(
                read_json(args.run / "bundle.json"),
                read_json(args.run / "snapshot.json"),
                read_json(args.config),
                args.out,
                args.ledger,
                args.workers,
                args.max_task_chars,
                args.retry_failed,
                args.provider_retries,
            )
            print(f"validated response: {args.out / 'response.json'}")
            return 1 if response["provenance"]["metrics"]["failures"] else 0
        elif args.command == "questions":
            print(canonical(QuestionBox(args.ledger).list()))
        elif args.command == "link-questions":
            print(
                canonical(QuestionBox(args.ledger).link(args.questions, args.reviewer))
            )
        elif args.command == "refresh-policy":
            rows = refresh_policies(
                args.packets,
                args.ledger,
                args.out,
                read_json(args.config) if args.config else None,
                args.workers,
                args.max_task_chars,
            )
            print(canonical(rows))
        elif args.command == "replay-items":
            replay_items(
                args.packet,
                args.run,
                args.ledger,
                args.out,
                args.benchmark_only,
                args.repair_bonus_ids,
            )
            print(f"Offline response: {args.out / 'response.json'}")
        elif args.command == "rescore":
            write_new(
                args.out,
                weighted_score(
                    read_json(args.run / "evaluation.json"), read_json(args.policy)
                ),
            )
            print(f"Saved weighted score: {args.out}")
        elif args.command == "benchmark":
            results = run_benchmark(
                args.snapshots,
                read_json(args.rubric),
                read_json(args.policy),
                read_json(args.config),
                args.out,
                args.workers,
                args.max_task_chars,
                not args.no_variants,
                args.provider_retries,
            )
            print(f"Benchmark report: {args.out / 'report.md'}")
            return 0 if acceptance(results, not args.no_variants)["passed"] else 1
        elif args.command == "answer-policy":
            QuestionBox(args.ledger).answer(args.question, args.value, args.reviewer)
            print(
                "Policy saved. Use refresh-policy --packets ... to propagate "
                "it; unaffected item records are reused."
            )
        elif args.command == "judge":
            inputs = (
                read_json(args.run / "bundle.json"),
                read_json(args.run / "snapshot.json"),
                read_json(args.config),
                args.out,
            )
            if args.legacy_review:
                judge(*inputs)
            else:
                response = judge_items(
                    *inputs,
                    args.ledger or args.out.parent / "judgments",
                    args.workers,
                    args.max_task_chars,
                    args.retry_failed,
                    args.provider_retries,
                )
                print(f"validated response: {args.out / 'response.json'}")
                return 1 if response["provenance"]["metrics"]["failures"] else 0
            print(f"validated response: {args.out / 'response.json'}")
        elif args.command == "import-review":
            bundle, snapshot, review = (
                read_json(args.run / "bundle.json"),
                read_json(args.run / "snapshot.json"),
                read_json(args.review),
            )
            validate_review(review, bundle, snapshot)
            write_new(
                args.out,
                {
                    "bundle_id": bundle["id"],
                    "review": review,
                    "provenance": {"kind": args.kind, "reviewer": args.reviewer},
                },
            )
            print(f"validated {args.kind} review: {args.out}")
        elif args.command == "finalize":
            report = finalize(args.run, read_json(args.response), args.out)
            print(
                canonical(
                    {
                        key: report[key]
                        for key in (
                            "base_earned",
                            "base_max",
                            "base_pending",
                            "bonus_earned",
                        )
                    }
                )
            )
        elif args.command == "evidence":
            snapshot = read_json(args.snapshot)
            verify_snapshot(snapshot)
            text = snapshot["files"][args.path]["text"]
            if text is None:
                raise ValueError("File is not UTF-8 text")
            lines = text.splitlines()
            end = args.end if args.end is not None else len(lines)
            if not 1 <= args.start <= end <= len(lines):
                raise ValueError("Invalid line range")
            for number in range(args.start, end + 1):
                print(f"{number}: {lines[number - 1]}")
    except (ValueError, OSError, KeyError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
