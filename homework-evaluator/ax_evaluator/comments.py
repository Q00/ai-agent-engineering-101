"""Render concise student feedback and explicitly publish a verified draft."""

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from .storage import canonical, digest, read_json, write_new


def pending_kind(key, finding):
    if finding["status"] != "pending":
        return ""
    if "pending_reason" in finding:
        return finding["pending_reason"]
    if key == "C02" and "open 정책 보류 행: [" in finding["reason"]:
        return "policy"
    return "not-reviewed"


def publication_blockers(report):
    """Separate missing review work, student evidence and unsettled policy."""
    blockers = []
    failed = {
        f["task"]
        for f in report.get("provenance", {}).get("metrics", {}).get("failures", [])
    }
    for key, item in report["items"].items():
        kind = pending_kind(key, item)
        if kind not in {"", "policy"}:
            blockers.append(
                {"task": key, "kind": "evaluation-failure" if key in failed else kind}
            )
    blockers += [
        {"task": key, "kind": "unresolved-atomic-fact"}
        for key, value in report.get("provenance", {})
        .get("metrics", {})
        .get("atomic_decisions", {})
        .items()
        if value is None
    ]
    if not report["bonus_review_complete"]:
        blockers.append(
            {
                "task": "bonus",
                "kind": "evaluation-failure"
                if any(k.startswith("@") for k in failed)
                else "incomplete-bonus-review",
            }
        )
    blockers += [
        {"task": b["id"], "kind": "bonus-evidence"}
        for b in report["bonuses"]
        if b["status"] == "pending"
    ]
    blockers += [
        {"task": ",".join(c["items"]), "kind": "integration-conflict"}
        for c in report.get("integration_conflicts", [])
    ]
    if not report.get("success_audit"):
        blockers.append({"task": "audits", "kind": "not-reviewed"})
    else:
        blockers += [
            {"task": area, "kind": "incomplete-audit"}
            for area, audit in report["success_audit"].items()
            if audit["status"] == "insufficient"
        ]
    return blockers


def comment_complete(report):
    return not publication_blockers(report)


def source_links(finding, metadata):
    labels = []
    # Packet-only files such as @history.txt do not exist in the student's PR.
    # Keep their full references in feedback.md, not in public source links.
    visible = [ref for ref in finding["evidence"] if not ref["path"].startswith("@")]
    for ref in visible[:3]:
        label = f"{ref['path']}:{ref['start']}"
        if metadata.get("source") == "github" and not ref["path"].startswith("@"):
            path = (
                f"submissions/{metadata['student_id']}/{metadata['week']}/{ref['path']}"
            )
            url = (
                f"https://github.com/{metadata['repository']}/blob/"
                f"{metadata['head_sha']}/{quote(path)}#L{ref['start']}"
            )
            labels.append(f"[{label}]({url})")
        else:
            labels.append(f"`{label}`")
    return ", ".join(labels)


def render_comment(report, bundle, snapshot):
    incomplete = not comment_complete(report)
    metadata = snapshot["metadata"]
    findings = [report["items"][i["id"]] for i in bundle["rubric"]["items"]]
    findings += report["bonuses"]
    revisions = [
        f
        for f in findings
        if f.get("feedback_type") == "revision"
        and f.get("severity", "material") in {"material", "critical"}
    ]
    # Every automatic failure is actionable, even without model feedback fields.
    revisions += [
        f
        for f in report["items"].values()
        if f["status"] == "fail" and f.get("origin") == "code"
    ]
    strengths = [
        f
        for f in findings
        if f.get("feedback_type") == "strength" and f["status"] == "pass"
    ][:2]
    week = bundle["rubric"]["week"]
    lines = [
        f"{week} 검토 결과입니다."
        + (" 다음 내용을 수정해 주세요." if revisions else ""),
        "",
    ]
    if incomplete:
        lines[:0] = [
            "평가 미완료 — 게시할 수 없습니다. "
            "미검토·증거 보류·성공 판정 검토를 확인하세요.",
            "확인된 피드백만 담은 부분 초안입니다.",
            "",
        ]
        labels = {
            "evaluation-failure": "평가 처리 미완료",
            "evidence": "제출 근거 확인 필요",
            "not-reviewed": "미검토",
            "incomplete-bonus-review": "추가 시도 검토 미완료",
            "bonus-evidence": "가산 근거 확인 필요",
            "integration-conflict": "항목 간 모순 확인 필요",
            "incomplete-audit": "관련 항목 검토 미완료",
        }
        lines[3:3] = [
            "게시 전 확인: "
            + "; ".join(
                b["task"] + " (" + labels.get(b["kind"], b["kind"]) + ")"
                for b in publication_blockers(report)
            ),
            "평가 처리 실패는 학생의 결함이나 감점 사유가 아닙니다.",
            "",
        ]
    lines += [" ".join(f["feedback"] for f in strengths), ""] if strengths else []
    groups = {}
    for index, finding in enumerate(revisions):
        # Semantic grouping is explicit in the review, never guessed from wording.
        key = (
            ("review", finding["revision_key"])
            if finding.get("revision_key")
            else ("automatic", index)
        )
        groups.setdefault(key, []).append(finding)
    for members in groups.values():
        finding = members[0]
        prefix = "추가 작업 보완: " if "contribution" in finding else ""
        feedback = finding.get("feedback", finding.get("reason", ""))
        lines += [f"- {prefix}{feedback}"]
        evidence = []
        for member in members:
            for ref in member["evidence"]:
                if ref not in evidence:
                    evidence.append(ref)
        refs = source_links({"evidence": evidence}, metadata)
        if refs:
            lines.append(f"  근거: {refs}")
        reruns = {member.get("rerun", "unknown") for member in members}
        decision = (
            "필요"
            if "needed" in reruns
            else "확인 필요"
            if "unknown" in reruns
            else "불필요"
        )
        reasons = list(
            dict.fromkeys(
                member.get("rerun_reason", "필요 여부를 채점자가 확인해야 합니다.")
                for member in members
            )
        )
        lines.append(f"  재실험 {decision}: " + " ".join(reasons))
    if revisions:
        reruns = {f.get("rerun", "unknown") for f in revisions}
        lines += [
            "",
            "재실험: "
            + (
                "수정 항목 중 재실행이 필요한 항목이 있습니다. 상세 평가를 확인하세요."
                if "needed" in reruns
                else "필요 여부를 채점자가 확인해야 합니다."
                if "unknown" in reruns
                else "요청한 수정은 새 모델 실험 없이 반영할 수 있습니다."
            ),
        ]
    extras = [b for b in report["bonuses"] if b["status"] == "pass"]
    if extras:
        lines += [
            "",
            "추가 시도 중 "
            + ", ".join(b["title"] for b in extras)
            + "의 기여와 검증 기록도 확인했습니다.",
        ]
    if any(pending_kind(k, f) == "policy" for k, f in report["items"].items()):
        lines += [
            "",
            "일부 점수 기준은 교수자 확인 사항이며 "
            "학생 수정 요청에 포함하지 않았습니다.",
        ]
    lines += [
        "",
        "제출 코드·기록을 검토한 초안입니다. 학생 프로그램을 재실행하지 않았습니다.",
    ]
    return "\n".join(lines) + "\n"


def write_comment(out, report, bundle, snapshot):
    body = render_comment(report, bundle, snapshot)
    write_new(out / "pr-comment.md", body)
    meta = snapshot["metadata"]
    manifest = {
        "bundle_id": bundle["id"],
        "body_id": digest(body),
        "complete": comment_complete(report),
        "blockers": publication_blockers(report),
        "benchmark_only": bool(
            meta.get("benchmark_only")
            or report.get("provenance", {}).get("benchmark_only")
        ),
        "repository": meta.get("repository"),
        "pr": meta.get("pr"),
        "head_sha": meta.get("head_sha"),
        "week": bundle["rubric"]["week"],
    }
    manifest["id"] = digest(manifest)
    write_new(out / "publication.json", manifest)


def github(endpoint, method="GET", payload=None, paginate=False):
    args = ["gh", "api", endpoint, "--method", method]
    if paginate:
        args += ["--paginate", "--slurp"]
    if payload is not None:
        args += ["--input", "-"]
    result = subprocess.run(
        args,
        input=canonical(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"GitHub {method} failed; no automatic retry")
    value = json.loads(result.stdout)
    return [item for page in value for item in page] if paginate else value


def publish(final_dir, write=False):
    """No network on preview. --write updates only this account's marked comment."""
    root = Path(final_dir)
    manifest = read_json(root / "publication.json")
    if manifest["id"] != digest({k: v for k, v in manifest.items() if k != "id"}):
        raise ValueError("Publication manifest changed")
    body = (root / "pr-comment.md").read_text()
    if digest(body) != manifest["body_id"]:
        raise ValueError(
            "Comment changed after generation; regenerate before publishing"
        )
    if not manifest["complete"]:
        raise ValueError("Incomplete evaluations cannot be published")
    if write and manifest.get("benchmark_only"):
        raise ValueError("Benchmark artifacts cannot be published to student PRs")
    repo, number = manifest["repository"], manifest["pr"]
    if not isinstance(repo, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Publishing needs a GitHub PR snapshot")
    if type(number) is not int or number < 1:
        raise ValueError("Invalid PR number")
    marker = f"<!-- ax-homework-evaluator:{repo}:{number}:{manifest['week']} -->"
    posted_body = marker + "\n" + body + f"\n평가 제출 커밋: `{manifest['head_sha']}`\n"
    if not write:
        return {
            "action": "preview-only",
            "repository": repo,
            "pr": number,
            "body": posted_body,
        }
    pr = github(f"repos/{repo}/pulls/{number}")
    if pr["head"]["sha"] != manifest["head_sha"] or pr["state"] != "open":
        raise ValueError("PR head changed or PR closed; collect and evaluate again")
    login = github("user")["login"]
    comments = github(
        f"repos/{repo}/issues/{number}/comments?per_page=100", paginate=True
    )
    ours = [
        c
        for c in comments
        if c["user"]["login"] == login and c["body"].startswith(marker + "\n")
    ]
    if len(ours) > 1:
        raise ValueError("Multiple harness comments found; resolve duplicates first")
    if ours and ours[0]["body"] == posted_body:
        return {"action": "unchanged", "url": ours[0]["html_url"]}
    # Recheck immediately before writing; GitHub comments have no atomic head guard.
    current = github(f"repos/{repo}/pulls/{number}")
    if current["head"]["sha"] != manifest["head_sha"] or current["state"] != "open":
        raise ValueError("PR changed during publication")
    endpoint = (
        f"repos/{repo}/issues/comments/{ours[0]['id']}"
        if ours
        else f"repos/{repo}/issues/{number}/comments"
    )
    value = github(endpoint, "PATCH" if ours else "POST", {"body": posted_body})
    return {"action": "updated" if ours else "created", "url": value["html_url"]}
