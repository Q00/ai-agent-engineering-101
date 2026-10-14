"""Explicit, sequential PR batches with saved inputs and resumable results."""

import fcntl
import re
from collections import Counter
from pathlib import Path

from . import __version__
from .comments import comment_complete
from .policy import apply_policy
from .providers import judge, validate_config
from .review import ROOT, finalize, prepare, validate_review, validate_rubric
from .snapshot import collect_pr, validate_week, verify_snapshot
from .storage import canonical, digest, read_json, write_new


def save_progress(root, progress):
    """Only the operational checkpoint is replaced; evidence stays immutable."""
    temporary = root / "progress.tmp"
    temporary.write_text(canonical(progress), encoding="utf-8")
    temporary.replace(root / "progress.json")


def next_path(root, pr, name, suffix=""):
    index = 1
    while True:
        path = root / f"pr-{pr}" / f"{name}-{index:03d}{suffix}"
        if not path.exists():
            return path
        index += 1


def batch_inputs(
    repo, prs, week, spec_ref, rubric, policy, config, max_chars, per_file
):
    validate_week(week)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Expected owner/repository")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", spec_ref):
        raise ValueError("Batch spec-ref must be a full commit SHA, not a branch")
    if not prs or any(type(pr) is not int or pr <= 0 for pr in prs):
        raise ValueError("Batch needs positive PR numbers")
    if len(set(prs)) != len(prs):
        raise ValueError("Duplicate PR numbers")
    if max_chars <= 0 or per_file <= 0:
        raise ValueError("Evidence limits must be positive")
    rubric = apply_policy(rubric, policy)
    validate_rubric(rubric)
    if rubric["week"] != week:
        raise ValueError("Batch and rubric week differ")
    if config is not None:
        validate_config(config)
    return {
        "version": __version__,
        "implementation_id": digest(
            {
                path.name: path.read_text()
                for path in sorted((ROOT / "ax_evaluator").glob("*.py"))
            }
        ),
        "instructions_id": digest(
            (ROOT / "skills/assignment-review/SKILL.md").read_text()
        ),
        "repository": repo,
        "prs": prs,
        "week": week,
        "spec_sha": spec_ref.lower(),
        "rubric": rubric,
        "config": config,
        "max_chars": max_chars,
        "per_file": per_file,
    }


def process_pr(root, progress, pr, stage):
    item = progress["items"][str(pr)]
    inputs = progress["inputs"]

    def checkpoint(step, **fields):
        item.update(status="running", stage=step, **fields)
        save_progress(root, progress)

    snapshot = None
    if item.get("snapshot"):
        snapshot = read_json(root / item["snapshot"])
        verify_snapshot(snapshot)
    if snapshot is None:
        if stage == "finalize":
            item["status"] = "awaiting-prepare"
            return
        path = next_path(root, pr, "snapshot", ".json")
        checkpoint("collect")
        snapshot = collect_pr(
            inputs["repository"], pr, path, inputs["spec_sha"], week=inputs["week"]
        )
        item.update(
            snapshot=str(path.relative_to(root)),
            student_id=snapshot["metadata"]["student_id"],
            head_sha=snapshot["metadata"]["head_sha"],
        )
        save_progress(root, progress)

    if item.get("packet"):
        packet = root / item["packet"]
        bundle = read_json(packet / "bundle.json")
    else:
        if stage == "finalize":
            item["status"] = "awaiting-prepare"
            return
        packet = next_path(root, pr, "packet")
        checkpoint("prepare")
        bundle = prepare(
            snapshot, inputs["rubric"], packet, inputs["max_chars"], inputs["per_file"]
        )
        item["packet"] = str(packet.relative_to(root))
        save_progress(root, progress)
    if stage == "prepare":
        item["status"] = "prepared"
        return

    # A completed model response survives an interruption before finalize/checkpoint.
    response_path = root / item["response"] if item.get("response") else None
    if response_path is not None and response_path.exists():
        response = read_json(response_path)
    elif stage == "finalize":
        response_path = root / f"pr-{pr}" / "review.json"
        if not response_path.exists():
            item["status"] = "awaiting-review"
            return
        response = read_json(response_path)
        item["response"] = str(response_path.relative_to(root))
    else:
        model_out = next_path(root, pr, "model")
        checkpoint(
            "judge", response=str((model_out / "response.json").relative_to(root))
        )
        response = judge(bundle, snapshot, inputs["config"], model_out)
    checkpoint("finalize")
    if response.get("bundle_id") != bundle["id"]:
        raise ValueError("Response belongs to another evidence packet")
    validate_review(response["review"], bundle, snapshot)
    final_out = next_path(root, pr, "final")
    report = finalize(packet, response, final_out)
    item.update(
        status="complete" if comment_complete(report) else "needs-review",
        final=str(final_out.relative_to(root)),
        base_earned=report["base_earned"],
        base_max=report["base_max"],
        base_pending=report["base_pending"],
        bonus_earned=report["bonus_earned"],
        qualitative_pending=report["qualitative_pending"],
        comment_ready=comment_complete(report),
    )


def summarize(root, progress):
    rows = []
    lines = [
        "# PR 일괄 평가 결과",
        "",
        "사람 검토용 초안입니다. 학생 프로그램 실행 및 PR 게시는 하지 않았습니다.",
        "점수는 기본·가산을 분리하며, "
        "완료 상태는 교수자의 성적 확정을 뜻하지 않습니다.",
        "",
        "| PR | 학번 | 상태 | 기본점수 | 가산 | 기본 보류 | 결과 |",
        "|---|---|---|---|---|---|---|",
    ]
    for pr in progress["inputs"]["prs"]:
        item = progress["items"][str(pr)]
        row = {"pr": pr, **item}
        rows.append(row)
        points = (
            f"{item['base_earned']}/{item['base_max']}"
            if "base_earned" in item
            else "—"
        )
        link = (
            f"[상세]({item['final']}/feedback.md) · "
            f"[코멘트]({item['final']}/pr-comment.md)"
            if item.get("final")
            else "—"
        )
        status = item["status"]
        if status == "failed":
            status += f" ({item.get('stage', '')}: {item.get('error_type', '')})"
        lines.append(
            f"| #{pr} | {item.get('student_id', '—')} | {status} | {points} | "
            f"{item.get('bonus_earned', '—')} | "
            f"{item.get('base_pending', '—')} | {link} |"
        )
    summary = {
        "batch_id": progress["id"],
        "counts": dict(Counter(row["status"] for row in rows)),
        "items": rows,
    }
    # Summaries are derived views. Earlier evidence and attempt directories remain.
    (root / "summary.json").write_text(canonical(summary), encoding="utf-8")
    (root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def run_batch(
    repo,
    prs,
    week,
    spec_ref,
    rubric,
    policy,
    out,
    *,
    config=None,
    stage="prepare",
    resume=False,
    retry_failed=False,
    max_chars=180_000,
    per_file=12_000,
    notify=print,
):
    """No discovery, scheduling, publishing, or implicit model retries."""
    if stage not in {"prepare", "evaluate", "finalize"}:
        raise ValueError("Invalid batch stage")
    if stage == "evaluate" and config is None:
        raise ValueError("Batch evaluate needs an explicit model config")
    if retry_failed and not resume:
        raise ValueError("retry-failed requires resume")
    inputs = batch_inputs(
        repo, prs, week, spec_ref, rubric, policy, config, max_chars, per_file
    )
    root = Path(out)
    if resume:
        if not (root / "progress.json").is_file():
            raise ValueError("No batch checkpoint to resume")
    elif root.exists():
        raise ValueError("Choose a new batch directory or use resume")
    else:
        root.mkdir(parents=True)
    # Advisory OS lock releases even if the process is interrupted or killed.
    with (root / ".batch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("This batch is already running") from error
        if resume:
            progress = read_json(root / "progress.json")
            if progress["id"] != digest(inputs) or progress["inputs"] != inputs:
                raise ValueError("Batch inputs/code changed; use a new batch directory")
        else:
            progress = {
                "id": digest(inputs),
                "inputs": inputs,
                "items": {str(pr): {"status": "pending"} for pr in prs},
            }
            write_new(root / "batch.json", {"id": progress["id"], "inputs": inputs})
            save_progress(root, progress)
        try:
            for index, pr in enumerate(prs, 1):
                item = progress["items"][str(pr)]
                # An interrupted request may have been charged. Require explicit retry.
                if item["status"] == "running" and item.get("stage") == "judge":
                    response = root / item["response"]
                    if not response.exists():
                        item.update(status="failed", error_type="InterruptedModelCall")
                if item["status"] in {"complete", "needs-review"}:
                    notify(f"[{index}/{len(prs)}] PR #{pr}: saved {item['status']}")
                    continue
                if item["status"] == "failed" and not retry_failed:
                    notify(f"[{index}/{len(prs)}] PR #{pr}: failed; use --retry-failed")
                    continue
                notify(f"[{index}/{len(prs)}] PR #{pr}: {stage}")
                try:
                    item.pop("error_type", None)
                    process_pr(root, progress, pr, stage)
                except Exception as error:
                    # Provider exception bodies may contain secrets; omit them.
                    item.update(status="failed", error_type=type(error).__name__)
                    write_new(
                        next_path(root, pr, "failure", ".json"),
                        {
                            "stage": item.get("stage"),
                            "error_type": type(error).__name__,
                        },
                    )
                save_progress(root, progress)
                notify(f"[{index}/{len(prs)}] PR #{pr}: {item['status']}")
        finally:
            save_progress(root, progress)
            summary = summarize(root, progress)
        return summary
