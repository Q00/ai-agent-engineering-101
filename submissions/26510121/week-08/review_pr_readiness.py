"""Read-only PR audit; output paths and counts, never credential values."""
from collections import Counter
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
PREFIX = "submissions/26510121/"
VISUALLY_REVIEWED_COMMIT = "239e162"


def git(*args):
    return subprocess.check_output(
        ["git", "-c", "safe.directory=" + REPO.as_posix(), *args], cwd=REPO)


def forbidden(path):
    name = Path(path).name.lower()
    return name == ".env" or name.endswith(".pem") or (
        name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"})


def public_hash_match_count(body, pattern):
    """Classify known false positives without printing any matched string."""
    try:
        data = json.loads(body)
        rows = data.get("events", {}).get("public_settle", [])
    except (ValueError, AttributeError):
        return 0
    total = 0
    for row in rows:
        try:
            event = json.loads(row[3])
            value = event.get("checkout_hash", "")
            if (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", value)
                    and len(base64.urlsafe_b64decode(value + "=")) == 32):
                total += len(pattern.findall(value.encode()))
        except (ValueError, IndexError, KeyError, TypeError, AttributeError):
            continue
    return total


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    changed = git("diff", "--name-only", "upstream/main...HEAD").decode().splitlines()
    tracked = git("ls-files", "--", PREFIX).decode().splitlines()
    pending = git("status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
    status = {line[3:]: line[:2] for line in pending}
    result = {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "head": git("rev-parse", "HEAD").decode().strip(),
        "branch": git("branch", "--show-current").decode().strip(),
        "upstream": git("rev-parse", "upstream/main").decode().strip(),
        "scope_by_week": dict(Counter(p.split("/")[2] if p.startswith(PREFIX)
                                      else "outside_student" for p in changed)),
        "outside_student_paths": [p for p in changed if not p.startswith(PREFIX)],
        "unmerged_paths": git("diff", "--name-only", "--diff-filter=U").decode().splitlines(),
        "pending_status_counts": dict(Counter(status.values())),
        "pending_week08_count": sum(p.startswith(PREFIX + "week-08/") for p in status),
        "forbidden_tracked_paths": [p for p in tracked if forbidden(p)],
        "week08_checker_exists": (REPO / "scripts" / "check_week08.py").exists(),
    }
    result["required_files"] = []
    for name in ("REPORT.md", "architecture.tex", "architecture.pdf", "logs/market-kit.log"):
        path = ROOT / name
        relative = path.relative_to(REPO).as_posix()
        result["required_files"].append(dict(path=name, exists=path.is_file(),
            bytes=path.stat().st_size if path.exists() else 0, tracked=relative in tracked,
            pending_status=status.get(relative)))
    tex = (ROOT / "architecture.tex").read_text(encoding="utf-8-sig")
    references = set()
    for block in re.findall(r"\\texttt\{([^{}]+)\}", tex):
        if "/" in block:
            references.update(part.strip().replace(r"\_", "_").split(".")[0]
                              for part in block.split("/"))
    result["architecture_module_references"] = dict(count=len(references), missing=[name
        for name in sorted(references) if not any((base / (name + ".py")).exists()
            for base in (ROOT, ROOT / "r3"))])
    from importlib.util import find_spec
    if find_spec("pymupdf"):
        import pymupdf
        with pymupdf.open(ROOT / "architecture.pdf") as doc:
            reviewed_pdf = git("show", VISUALLY_REVIEWED_COMMIT + ":" + PREFIX + "week-08/architecture.pdf")
            result["architecture_pdf"] = dict(pages=len(doc), outside_text_blocks=sum(
                not page.rect.contains(pymupdf.Rect(block[:4])) for page in doc for block in page.get_text("blocks")),
                matches_visually_reviewed_pdf=hashlib.sha256((ROOT / "architecture.pdf").read_bytes()).digest()
                    == hashlib.sha256(reviewed_pdf).digest(),
                visual_review_evidence="PDF in commit " + VISUALLY_REVIEWED_COMMIT + " was rendered and manually inspected on 2026-11-21: no overlapping boxes or clipped text.")
    release = json.loads((ROOT / "logs" / "v34-release-20261117.json").read_text(encoding="utf-8-sig"))
    result["v34_runtime_source_mismatches"] = [name for name, digest in release["source_hashes"].items()
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    policy = json.loads((ROOT / "policy.json").read_text(encoding="utf-8-sig"))
    result["final_policy"] = {key: policy.get(key) for key in (
        "shop_strategy_revision", "buyer_strategy_revision", "llm_disabled", "research_enabled",
        "capability_llm_enabled", "finalday_departments_enabled", "market_end_at")}
    report = (ROOT / "REPORT.md").read_text(encoding="utf-8-sig")
    result["report_still_contains_incomplete_markers"] = [phrase for phrase in (
        "완성된 제출 보고서가 아니다", "실제 Round 3 운영 결과: 아직 미검증",
        "실제 운영 후 로그와 board를 인용해 추가해야 한다") if phrase in report]
    board_path = ROOT / ".market" / "board.json"
    if board_path.exists():
        board = json.loads(board_path.read_text(encoding="utf-8-sig"))
        result["local_board_metadata"] = {k: board[k] for k in (
            "current", "round", "at", "updated_at", "generated_at", "frozen", "closed") if k in board}
        result["local_board_own_rows"] = {key: [{k: row.get(k) for k in (
            "name", "rank", "all", "ALL", "deals", "settled", "settlements", "book", "saved", "score")
            if k in row} for row in board.get(key, []) if row.get("name") == "seochanit"]
            for key in ("shops", "buyers", "players") if isinstance(board.get(key), list)}
        result["local_board_is_official_closing_snapshot_verified"] = False

    # Enumerate all novel reachable objects, including superseded code/log versions.
    objects = {}
    historical_forbidden = []
    for line in git("rev-list", "--objects", "HEAD", "^upstream/main", "--", PREFIX).decode().splitlines():
        sha, _, path = line.partition(" ")
        objects.setdefault(sha, path)
        if path.startswith(PREFIX) and forbidden(path):
            historical_forbidden.append(dict(blob=sha, path=path))
    patterns = {
        "openai_key_shape": re.compile(rb"sk" + rb"-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}"),
        "github_token_shape": re.compile(rb"(?:gh[pousr]_" + rb"[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
        "private_key_header": re.compile(rb"-----BEGIN " + rb"(?:(?:RSA|EC|OPENSSH|DSA) )?PRIVATE KEY-----"),
    }
    hits = []
    checked_blobs = checked_bytes = 0
    process = subprocess.Popen(["git", "-c", "safe.directory=" + REPO.as_posix(), "cat-file", "--batch"],
                               cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for sha, path in objects.items():
            process.stdin.write((sha + "\n").encode()); process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3:
                raise RuntimeError("Unexpected cat-file header")
            size = int(header[2]); body = process.stdout.read(size)
            if len(body) != size or process.stdout.read(1) != b"\n":
                raise RuntimeError("Incomplete cat-file payload")
            if header[1] != b"blob":
                continue
            checked_blobs += 1; checked_bytes += size
            for kind, pattern in patterns.items():
                count = len(pattern.findall(body))
                if count:
                    hits.append(dict(location="history", blob=sha, path=path, kind=kind, count=count))
    finally:
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("cat-file failed")
    for path in sorted(set(tracked) | {p for p in status if p.startswith(PREFIX)}):
        absolute = REPO / path
        if not absolute.is_file():
            continue
        body = absolute.read_bytes()
        for kind, pattern in patterns.items():
            count = len(pattern.findall(body))
            if count:
                hit = dict(location="worktree", path=path, kind=kind, count=count)
                if kind == "openai_key_shape" and public_hash_match_count(body, pattern) == count:
                    hit["classification"] = "All matches are in public_settle.checkout_hash, with 32-byte base64url hash shape; false positives."
                hits.append(hit)
    result["credential_shape_scan"] = dict(scope="student history unique to HEAD vs upstream/main, plus tracked and unignored pending student files",
        blobs_checked=checked_blobs, history_bytes_checked=checked_bytes, matches=hits,
        forbidden_historical_paths=historical_forbidden,
        limitation="Heuristic patterns only; matches require review and zero matches do not prove absence of all secrets.")
    result["verification"] = dict(
        default_run="8 failed, 669 passed, 1 warning",
        default_log="logs/verification-20261121T053348097594Z.txt",
        diagnostic="The same 8 failures pass with an advancing wall clock anchored at 2026-11-17T04:40:00Z. No runtime/test/policy changes were made.",
        diagnostic_log="logs/pr-readiness-clock-diagnostic-20261121T054127379377Z.txt",
        interpretation="All 8 failures depend on the ended market planning/reserve horizon. The normal command still fails; diagnostic is not a default pass.")
    result["github_pr_search"] = dict(repository="Q00/ai-agent-engineering-101",
        query='author:seochanit "week-08"', state="all", matches=0,
        observation_date="2026-11-21",
        observation="Connected GitHub PR search returned an empty issues list on the observation date; no PR was created or branch published in that review task. This is a recorded observation, not a new GitHub query on script reruns.")
    result["readiness"] = "incomplete"
    result["remaining"] = [
        "Finish REPORT results with timestamped settlements, Players Class rank, refusal log quotes and week-10 baseline comparison.",
        "Verify official Round-3 closing board provenance; later local snapshots are not final-ranking evidence by themselves.",
        "Make normal local verification reproducible after the configured market end; clock-shift diagnostic is not the default test result.",
        "Resolve inherited week-07 diff and select which uncommitted raw logs/supporting evidence belong in the PR.",
        "Publish the reviewed branch and open the week-08 PR before 2026-11-23 23:59 KST; original logs and commit history must remain intact.",
    ]
    out = ROOT / "logs" / ("pr-readiness-" + stamp + ".json")
    with out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    print("Audit:", out)
    print(json.dumps({k: result[k] for k in ("scope_by_week", "pending_status_counts", "pending_week08_count",
        "forbidden_tracked_paths", "week08_checker_exists", "v34_runtime_source_mismatches")}, ensure_ascii=False))
    print("History blobs / bytes:", checked_blobs, checked_bytes)
    print("Potential credential matches:", len(hits))
    for hit in hits:
        print(json.dumps(hit, ensure_ascii=False))


if __name__ == "__main__":
    main()
