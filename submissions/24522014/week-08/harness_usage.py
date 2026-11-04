"""Read-only model usage, by role and by the model actually called, to compare before and after a change.

The actual model comes from the recorded command line, not the metadata label. Codex echoes the prompt to stderr, and
seller and buyer prompts may contain words like "limit", so usage-limit wording is searched only in failed runs and only
after the echoed prompt. Tokens are what the CLI printed, summed as reported.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from statistics import median

from buyer_funnel import ROOT, funnel, load_runs, read_json, stamp

LIMIT_WORDS = re.compile(r"usage limit|rate.?limit|too many requests|\b429\b|quota|limit reached|exceeded your|insufficient_quota", re.I)
TOKENS = re.compile(r"tokens used\s+([\d,]+)\s*$")
SELLER_FAILURE = "[harness] decision failed"


def role_of(prompt):
    head = prompt[:120]
    return "buyer" if "Round 3 buyer" in head else "seller" if "sales agent" in head else "other"


def actual_model(metadata):
    command = metadata.get("command") or []
    return command[command.index("--model") + 1] if "--model" in command[:-1] else None


def percentile(values, share):
    return sorted(values)[min(len(values) - 1, int(len(values) * share))] if values else None


def load_calls(root, since, until=None):
    calls = []
    folder = Path(root) / "logs/harness"
    if not folder.is_dir():
        return calls
    for path in sorted(folder.iterdir()):
        name = path.name.split("-")[0]
        if name < since or (until and name >= until):
            continue
        metadata = read_json(path / "metadata.json")
        if not isinstance(metadata, dict) or metadata.get("status") == "running":
            continue
        try:
            prompt = (path / "request.txt").read_text(encoding="utf-8")
            stderr = (path / "stderr.txt").read_text(encoding="utf-8", errors="replace")
        except OSError:
            prompt, stderr = "", ""
        after_prompt = stderr[stderr.rfind(prompt[-200:]) + 200:] if prompt and prompt[-200:] in stderr else stderr
        failed = metadata.get("status") == "failed"
        error = str(metadata.get("error", ""))
        tokens = TOKENS.search(stderr.rstrip())
        calls.append({"name": name, "role": role_of(prompt), "model": actual_model(metadata),
                      "label": metadata.get("model"), "failed": failed,
                      "timeout": failed and "exceeded" in error and "seconds" in error,
                      "limit_error": failed and bool(LIMIT_WORDS.search(error + "\n" + after_prompt)),
                      "elapsed": metadata.get("elapsed_seconds"),
                      "tokens": int(tokens[1].replace(",", "")) if tokens else None})
    return calls


def summarize_calls(calls):
    groups = defaultdict(list)
    for call in calls:
        groups[f"{call['role']}|{call['model']}"].append(call)
    report = {}
    for key, group in sorted(groups.items()):
        elapsed = [c["elapsed"] for c in group if type(c["elapsed"]) in (int, float)]
        tokens = [c["tokens"] for c in group if c["tokens"] is not None]
        per_hour = Counter(c["name"][:11] for c in group)
        report[key] = {"calls": len(group), "failed": sum(c["failed"] for c in group),
                       "failure_rate": round(sum(c["failed"] for c in group) / len(group), 4),
                       "timeouts": sum(c["timeout"] for c in group), "usage_limit_errors": sum(c["limit_error"] for c in group),
                       "elapsed_median": median(elapsed) if elapsed else None, "elapsed_p90": percentile(elapsed, .9),
                       "elapsed_max": max(elapsed) if elapsed else None, "tokens_sum": sum(tokens),
                       "tokens_median": median(tokens) if tokens else None,
                       "max_calls_in_one_utc_hour": max(per_hour.values()),
                       "label_differs_from_actual": sum(c["label"] != c["model"] for c in group)}
    return report


def seller_failures(root, since, until=None, today=None):
    """Count kit log failure lines. The log has times only, so dates are recovered walking back from today."""
    path = Path(root) / "logs/market-kit.log"
    if not path.exists():
        return None
    day = today or datetime.now(timezone.utc).astimezone().date()
    previous = None
    count = 0
    for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        match = re.match(r"(\d\d):(\d\d):(\d\d) ", line)
        if not match:
            continue
        moment = tuple(int(part) for part in match.groups())
        if previous is not None and moment > previous:
            day -= timedelta(days=1)
        previous = moment
        if SELLER_FAILURE not in line:
            continue
        local = datetime(day.year, day.month, day.day, *moment).astimezone()
        at = local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if at >= since and (not until or at < until):
            count += 1
    return count


def hook_durations(root, since, until=None):
    """A trade's own span: buyer log folder created to record written. Skipped hooks have no trade."""
    spans = []
    runs = load_runs(root, since, until)
    for name, record in runs:
        folder = Path(str(record.get("buyer_log_dir", "")).replace("\\", "/")).name
        if not folder:
            continue
        begin = datetime.strptime(folder.split("-")[0], "%Y%m%dT%H%M%S%fZ")
        end = datetime.strptime(name, "%Y%m%dT%H%M%S%fZ")
        spans.append((end - begin).total_seconds())
    first, last = (runs[0][0], runs[-1][0]) if runs else (None, None)
    hours = None
    if runs and first != last:
        hours = (datetime.strptime(last, "%Y%m%dT%H%M%S%fZ") - datetime.strptime(first, "%Y%m%dT%H%M%S%fZ")).total_seconds() / 3600
    return {"hook_runs": len(runs), "hook_runs_per_hour": round(len(runs) / hours, 1) if hours else None,
            "trades": len(spans), "trade_seconds_median": round(median(spans), 2) if spans else None,
            "trade_seconds_p90": round(percentile(spans, .9), 2) if spans else None,
            "trade_seconds_max": round(max(spans), 2) if spans else None}


def report(root, since, until=None):
    calls = load_calls(root, since, until)
    limit_errors = sum(c["limit_error"] for c in calls)
    seller_failed = sum(c["failed"] for c in calls if c["role"] == "seller")
    kit_failures = seller_failures(root, since, until)
    purchase = funnel(root, since, until)
    return {"window": {"since": since, "until": until}, "by_role_and_actual_model": summarize_calls(calls),
            "seller_failures": {"harness_calls_failed": seller_failed, "kit_log_decision_failed": kit_failures},
            "usage_limit_errors": limit_errors, "hooks": hook_durations(root, since, until),
            "funnel": {row["stage"]: row["count"] for row in purchase["funnel"]},
            "decision_paths": purchase["decision_paths"], "causes": purchase["causes"],
            "rollback_signal": bool(limit_errors or seller_failed or kit_failures)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until")
    args = parser.parse_args()
    print(json.dumps(report(ROOT, stamp(args.since), stamp(args.until) if args.until else None), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
