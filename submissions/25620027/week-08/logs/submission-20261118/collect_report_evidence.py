"""Rebuild submission evidence from preserved local R3 data; no network or writes to the agent."""

from __future__ import annotations

import ast
import collections
import hashlib
import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    board_path = OUT / "board-postclose-20261118T0959.json"
    baseline_path = ROOT / ".omo/evidence/telegram-factors-20261109T234640Z/verification.json"
    board = json.loads(board_path.read_text())
    baseline = json.loads(baseline_path.read_text())
    spec = importlib.util.spec_from_file_location("submission_standing", ROOT / "r3/standing.py")
    assert spec is not None and spec.loader is not None
    standing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standing)
    result = standing.standing(board, "25620027")
    assert result["shop_reconstruction"]["status"] == "matched"
    assert result["buyer_reconstruction"]["status"] == "matched"
    result.pop("all_diagnostic", None)
    students = [p for p in board["players"] if p.get("student") is True and not p.get("house")]
    shops = standing.class_rows(board, students, "shops", "shop")
    own_shop = next(s for s in shops if s["name"] == "25620027")
    own_buyer = next(b for b in standing.class_rows(board, students, "buyers", "buyer") if b["name"] == "25620027")
    top = sorted(students, key=lambda p: p["student_total"], reverse=True)[:5]
    raw_log = ROOT / "logs/market-kit.log"
    lines = raw_log.read_text().splitlines()
    outcomes: collections.Counter[str] = collections.Counter()
    reasons: collections.Counter[str] = collections.Counter()
    examples: dict[str, dict[str, object]] = {}
    for lineno, line in enumerate(lines, 1):
        match = re.search(r"\[R3 BUYER\] outcome=(\w+).*?reason=(.*)$", line)
        if not match:
            continue
        outcome = match.group(1)
        outcomes[outcome] += 1
        try:
            reason = str(ast.literal_eval(match.group(2)))
        except (ValueError, SyntaxError):
            reason = match.group(2)
        if outcome == "agreed":
            continue
        if "receipt expected credit" in reason:
            category = "receipt_quality_or_value"
        elif "emergency line" in reason:
            category = "price_line"
        elif "NoStock" in reason or "no in-stock" in reason:
            category = "stock_or_conditions"
        elif "timeout" in reason or "timed out" in reason:
            category = "timeout"
        elif "operator directive" in reason:
            category = "external_directive"
        else:
            category = "other"
        reasons[category] += 1
        examples.setdefault(category, {"line": lineno, "text": line})
    safe_patterns = {
        "private_key": r"BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY",
        "anthropic_key": r"sk-ant-[A-Za-z0-9_-]{15,}",
        "openai_key": r"sk-(?:proj-)?[A-Za-z0-9_-]{30,}",
        "github_token": r"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}",
        "authorization_bearer": r"(?i)authorization\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9_.-]{20,}",
    }
    log_text = raw_log.read_text()
    secret_counts = {k: len(re.findall(pattern, log_text)) for k, pattern in safe_patterns.items()}
    assert not any(secret_counts.values()), "Review potential secret before using submission log"
    evidence = {
        "scope": "R3 documentation; no runtime, policy, deployment, Git or external publication changes",
        "official_requirements": {
            "week08": {"url": "https://github.com/Q00/ai-agent-engineering-101/blob/main/week-08.html", "blob": "26318d19e9beaed0e538bc8bde17b96771a6b97c"},
            "week10": {"url": "https://github.com/Q00/ai-agent-engineering-101/blob/main/week-10.html", "blob": "15e9d65a6c0be69ecc5067efb44a89b3e4a469ef"},
            "week11": {"url": "https://github.com/Q00/ai-agent-engineering-101/blob/main/weeks/week-11/README.md", "blob": "e8dff6f8fd9c7b93714b3ec8f031f259032d1c7c"},
        },
        "early_reference": {"observed_kst": "2026-11-10T08:53:30+09:00", "source": str(baseline_path.relative_to(ROOT)), "sha256": digest(baseline_path), "status": "earlier preserved operational reference; Saved definition differs from final period", "class": baseline["verification"]["class"], "components": baseline["components"]},
        "baseline": json.loads((OUT / "baseline-and-close-reconciliation.json").read_text())["baseline"],
        "close_reconciliation": json.loads((OUT / "baseline-and-close-reconciliation.json").read_text()),
        "postclose": {"observed_kst": "2026-11-18T09:59+09:00", "source": str(board_path.relative_to(ROOT)), "sha256": digest(board_path), "cutoff_kst": "2026-11-17T23:59:00+09:00", "frozen_grading_snapshot_identity_verified": False, "standing": result, "own_shop": own_shop, "own_buyer": own_buyer, "stars_equal_count": sum(s["stars_rank"] == own_shop["stars_rank"] for s in shops), "top5": [{"player": p["player"], "total": p["student_total"]} for p in top]},
        "log": {"source_host_path": "/docker/ax-week08-25620027/data/r3/logs/market-kit.log", "original_bytes_preserved": True, "sha256": digest(raw_log), "remote_sha256": "2ab8d21a7de39c77a1442d9df6650ff632d66a04ed4a6be4dfb37b09d3937d16", "remote_mtime_utc": "2026-11-17T14:59:53.810761+00:00", "lines": len(lines), "first_clock_utc": lines[0][:8], "last_clock_utc": lines[-1][:8], "scope_note": "last rotated segment; not full R3 history nor full hour cohort; clocks UTC", "seller_settlement_lines": sum("[acp] settled " in line for line in lines), "buyer_paid_lines": sum("[acp] paid " in line for line in lines), "outcomes": dict(outcomes), "outcome_reason_categories": dict(reasons), "reason_examples": examples, "traceback_lines": sum("Traceback" in line for line in lines), "secret_pattern_matches": secret_counts},
        "standing_code_sha256": digest(ROOT / "r3/standing.py"),
        "limits": ["instructor cutoff archive not publicly located; final score agrees across last preclose observation and two postclose observations, while live assets continue changing", "LAB baseline reconstructed from a contemporaneously preserved pre-change observation; primary baseline and result use the same later Saved definition", "last log segment cannot reproduce cumulative settlements or all mature refunds", "concurrent changes and nonrandom market conditions preclude isolated causal claims"],
    }
    assert evidence["log"]["sha256"] == evidence["log"]["remote_sha256"]
    output = OUT / "evidence.json"
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output.relative_to(ROOT)), "class_rank": result["rank"], "class_total": result["total"], "stars_ties": evidence["postclose"]["stars_equal_count"], "outcomes": dict(outcomes), "reasons": dict(reasons)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
