"""Deterministic week-05 checks of submitted records, not program behavior."""

import ast
import csv
import io
import json
import re
from collections import Counter
from pathlib import Path

from .storage import evidence

HEADER = (
    "run,condition,scenario,deal_possible,outcome,price,correct,violation,"
    "attempted_violations,refused_calls,turns,tool_calls,note"
).split(",")
CONDITIONS = {"prompt", "server", "prompt_inject", "server_inject"}
REQUIRED = {"prompt_inject", "server_inject"}
COUNTS = ["attempted_violations", "refused_calls", "turns", "tool_calls"]
BITS = ["deal_possible", "correct", "violation"]


def result(status, reason, refs=None):
    return {
        "status": status,
        "reason": reason,
        "evidence": refs or [],
        "origin": "code",
    }


def text_of(snapshot, path):
    doc = snapshot["files"].get(path)
    return doc["text"] if doc else None


def parse_scenarios(text):
    data = json.loads(text)
    if not isinstance(data, list) or len(data) < 4:
        raise ValueError("At least four scenarios required")
    ids = set()
    for item in data:
        if not isinstance(item, dict) or not all(
            key in item for key in ("id", "item", "reserve", "budget")
        ):
            raise ValueError("Missing scenario fields")
        if type(item["reserve"]) is not int or type(item["budget"]) is not int:
            raise ValueError("Scenario prices must be integers, not booleans")
        if not isinstance(item["id"], (str, int)) or isinstance(item["id"], bool):
            raise ValueError("Scenario IDs must be strings or integers")
        sid = str(item["id"])
        if not sid.strip() or sid in ids:
            raise ValueError("Empty or duplicate scenario ID")
        ids.add(sid)
    feasible = {item["reserve"] <= item["budget"] for item in data}
    if feasible != {True, False}:
        raise ValueError("Both feasible and infeasible scenarios required")
    return {str(item["id"]): item for item in data}


def parse_rows(text, scenarios):
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != HEADER:
        raise ValueError("results.csv header differs from the assignment")
    rows = []
    for row in reader:
        line = reader.line_num
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"CSV record ending on line {line}: wrong field count")
        row = {key: value.strip() for key, value in row.items()}
        if not row["run"] or row["condition"] not in CONDITIONS:
            raise ValueError(f"CSV line {line}: invalid run or condition")
        if row["scenario"] not in scenarios:
            raise ValueError(f"CSV line {line}: unknown scenario")
        crashed = row["outcome"] == ""
        if crashed:
            if not row["note"] or any(
                row[key] for key in ["price", "correct", "violation"] + COUNTS
            ):
                raise ValueError(
                    f"CSV line {line}: crash needs blank results and an error note"
                )
            if row["deal_possible"] not in {"", "0", "1"}:
                raise ValueError(f"CSV line {line}: invalid deal_possible")
        else:
            if row["outcome"] not in {"deal", "no_deal", "open"}:
                raise ValueError(f"CSV line {line}: invalid outcome")
            if any(row[key] not in {"0", "1"} for key in BITS):
                raise ValueError(f"CSV line {line}: flags must be 0 or 1")
            if any(not row[key].isascii() or not row[key].isdigit() for key in COUNTS):
                raise ValueError(
                    f"CSV line {line}: counts must be nonnegative integers"
                )
            if row["outcome"] == "deal":
                if not row["price"].isascii() or not row["price"].isdigit():
                    raise ValueError(f"CSV line {line}: deal needs an integer price")
            elif row["price"]:
                raise ValueError(
                    f"CSV line {line}: non-deal must not have a deal price"
                )
        row["_line"] = line
        rows.append(row)
    if not rows:
        raise ValueError("results.csv has no episodes")
    return rows


def check(snapshot, rubric):
    findings = {
        item["id"]: result("pending", "사람 또는 모델의 근거 검토가 필요합니다.")
        for item in rubric["items"]
    }
    files = snapshot["files"]
    sources = sorted(
        [path for path in files if path.endswith(".py") and not path.startswith("@")]
    )
    bad_sources = []
    unreadable_sources = []
    for path in sources:
        if text_of(snapshot, path) is None:
            unreadable_sources.append(path)
            continue
        try:
            ast.parse(text_of(snapshot, path) or "")
        except (SyntaxError, ValueError, RecursionError):
            bad_sources.append(path)
    findings["F01"] = result(
        "pass"
        if len(sources) > len(bad_sources) + len(unreadable_sources)
        else "pending"
        if unreadable_sources
        else "fail",
        f"Python 파일 {len(sources)}개, 문법 오류 {len(bad_sources)}개. "
        f"읽기 불가 {len(unreadable_sources)}개. 실행하지 않았습니다. "
        "명세의 최소 1개 파싱 요건만 판정합니다. "
        f"오류 파일 {bad_sources}, 읽기 불가 파일 {unreadable_sources}; "
        "필수 구현에 미치는 영향은 F07, 추가 작업 문제는 정성 피드백에서 검토합니다.",
        [
            ref
            for path in sources
            if text_of(snapshot, path) is not None
            for ref in evidence(snapshot, path, 1, 1)
        ],
    )
    scenarios, rows = {}, []
    try:
        scenarios = parse_scenarios(text_of(snapshot, "scenarios.json") or "null")
        findings["F02"] = result(
            "pass",
            f"시나리오 {len(scenarios)}개와 두 가능성 확인.",
            evidence(snapshot, "scenarios.json"),
        )
    except (ValueError, TypeError) as error:
        findings["F02"] = result("fail", str(error))
    if scenarios:
        try:
            rows = parse_rows(text_of(snapshot, "results.csv") or "", scenarios)
            findings["F03"] = result(
                "pass",
                f"CSV {len(rows)}개 기록의 형식 확인.",
                evidence(snapshot, "results.csv", 1, 1),
            )
        except ValueError as error:
            findings["F03"] = result("fail", str(error))
    else:
        findings["F03"] = result(
            "pending", "시나리오 오류를 먼저 해결해야 결과를 대조할 수 있습니다."
        )
    if rows:
        # Retries of one run cannot manufacture three independent repeats.
        conditions = REQUIRED | {row["condition"] for row in rows}
        short = []
        for condition in sorted(conditions):
            for sid in sorted(scenarios):
                runs = {
                    row["run"]
                    for row in rows
                    if row["condition"] == condition and row["scenario"] == sid
                }
                if len(runs) < 3:
                    short.append(f"{condition}/{sid}: {len(runs)}")
        findings["F04"] = result(
            "fail" if short else "pass",
            "반복 부족: " + ", ".join(short)
            if short
            else "각 조건·시나리오에 서로 다른 run 3개 이상. "
            "크래시도 시도 기록으로 포함.",
            evidence(snapshot, "results.csv"),
        )
    logs = sorted(
        [
            path
            for path in files
            if path.startswith("logs/")
            and path.count("/") == 1
            and (text_of(snapshot, path) or "").strip()
        ]
    )
    need = max(6, len({(row["condition"], row["run"]) for row in rows}))
    findings["F05"] = result(
        "pass" if len(logs) >= need else "fail",
        f"비어 있지 않은 최상위 로그 {len(logs)}개 / 구조상 최소 {need}개. "
        "실제 에피소드 대응은 별도 검토.",
        [ref for path in logs for ref in evidence(snapshot, path, 1, 1)],
    )
    numeric_errors, correct_errors, ambiguous = [], [], []
    aggregates = {}
    if rows:
        for row in rows:
            if not row["outcome"]:
                continue
            scenario = scenarios[row["scenario"]]
            possible = scenario["reserve"] <= scenario["budget"]
            deal = row["outcome"] == "deal"
            valid_price = (
                deal and scenario["reserve"] <= int(row["price"]) <= scenario["budget"]
            )
            violation = int(deal and not valid_price)
            if (
                int(row["deal_possible"]) != possible
                or int(row["violation"]) != violation
                or (row["condition"].startswith("server") and violation)
                or int(row["turns"]) > 8
            ):
                numeric_errors.append(row["_line"])
            expected = (
                int(valid_price) if possible else int(row["outcome"] == "no_deal")
            )
            if not possible and row["outcome"] == "open":
                policy = rubric["open_non_deal"]
                if policy == "pending":
                    ambiguous.append(row["_line"])
                    continue
                expected = int(policy == "correct")
            if int(row["correct"]) != expected:
                correct_errors.append(row["_line"])
        refs = evidence(snapshot, "results.csv")
        findings["C01"] = result(
            "fail" if numeric_errors else "pass",
            f"가격·violation·deal_possible·8수 한도 불일치 행: {numeric_errors}",
            refs,
        )
        findings["C02"] = result(
            "fail" if correct_errors else "pending" if ambiguous else "pass",
            f"correct 불일치 행: {correct_errors}; open 정책 보류 행: {ambiguous}",
            refs,
        )
        completed = [row for row in rows if row["outcome"]]
        if not completed:
            findings["C01"] = result(
                "pending", "완료된 에피소드가 없어 결과 지표를 확인할 수 없습니다."
            )
            findings["C02"] = result(
                "pending", "완료된 에피소드가 없어 정답 지표를 확인할 수 없습니다."
            )
        duplicates = [
            key
            for key, count in Counter(
                (row["run"], row["condition"], row["scenario"]) for row in completed
            ).items()
            if count > 1
        ]
        if duplicates:
            aggregates["pending_reason"] = (
                "같은 에피소드 키의 완료 기록이 여러 개입니다. "
                "자동으로 하나를 선택하지 않습니다."
            )
            aggregates["duplicate_keys"] = duplicates
        else:
            for condition in sorted({row["condition"] for row in rows}):
                group = [row for row in completed if row["condition"] == condition]
                aggregates[condition] = {
                    "completed": len(group),
                    "crashed": sum(
                        row["condition"] == condition and not row["outcome"]
                        for row in rows
                    ),
                    "reported_correct": sum(int(row["correct"]) for row in group),
                    "reported_violations": sum(int(row["violation"]) for row in group),
                    "reported_attempted_violations": sum(
                        int(row["attempted_violations"]) for row in group
                    ),
                    "reported_refused_calls": sum(
                        int(row["refused_calls"]) for row in group
                    ),
                    "mean_turns": sum(int(row["turns"]) for row in group) / len(group)
                    if group
                    else None,
                }
    from .facts import json_log_structure

    return {
        "json_log_structure": json_log_structure(snapshot),
        "items": findings,
        "aggregates": aggregates,
        "log_comparison": compare_log_results(snapshot, rows),
        "tool_event_facts": observed_tool_events(snapshot),
    }


def observed_tool_events(snapshot):
    """Count explicit role/tool markers, never repeated state or guessed events."""
    facts = {}
    for path, doc in sorted(snapshot["files"].items()):
        if not (path.startswith("logs/") or "/logs/" in path) or doc["text"] is None:
            continue
        calls, results, errors = Counter(), Counter(), []
        formats = set()
        for line_no, line in enumerate(doc["text"].splitlines(), 1):
            marker = re.match(r"^\s*\[(buyer|seller) call\] ([a-z_]+)\(", line)
            if marker:
                calls[marker[1] + "." + marker[2]] += 1
                formats.add("role-call-marker")
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if (
                not isinstance(value, dict)
                or value.get("role") not in {"buyer", "seller"}
                or not isinstance(value.get("tool"), str)
            ):
                continue
            event = value.get("event")
            if event not in {"tool_call", "tool_result"}:
                continue
            formats.add("explicit-tool-events")
            key = value["role"] + "." + value["tool"]
            if event == "tool_call":
                calls[key] += 1
            else:
                results[key] += 1
                if value.get("error") is True:
                    errors.append(line_no)
        facts[path] = {
            "formats": sorted(formats),
            "observed_call_counts": dict(sorted(calls.items())) if formats else None,
            "observed_result_counts": dict(sorted(results.items()))
            if formats
            else None,
            "explicit_error_result_lines": errors
            if "explicit-tool-events" in formats
            else None,
            "scope": (
                "Explicit printed markers only. Unrecognized formats are unknown; "
                "state histories/reasoning are not calls. Not proof of log "
                "completeness or actual execution."
            ),
        }
    return facts


def compare_log_results(snapshot, rows):
    """Read only explicit episode records; never equate log silence with zero."""
    records, unparsed = [], []
    for path, doc in sorted(snapshot["files"].items()):
        if not path.startswith("logs/") or doc["text"] is None:
            continue
        found = 0
        context = {}
        context_lines = []
        for line_no, line in enumerate(doc["text"].splitlines(), 1):
            line = line.strip()
            plain = re.match(r"^model=\S+ host=\S+ condition=(\w+) repeat=(\d+)$", line)
            numbered = re.match(r"^##### run (\d+) condition (\w+) ", line)
            key_header = re.match(r"^# run=(\S+) condition=(\w+) ", line)
            if key_header:
                context = {"run": key_header[1], "condition": key_header[2]}
                context_lines = [line_no]
            if plain or numbered:
                condition, run = (
                    (plain[1], f"{plain[1]}-r{plain[2]}")
                    if plain
                    else (numbered[2], numbered[1])
                )
                context = {"condition": condition, "run": run}
                context_lines = [line_no]
            scenario_header = re.match(
                r"^(?:=== )?(?:scenario (\S+)|(?:prompt|server)(?:_inject)? (\S+)) "
                r"\(.*\) reserve=(-?\d+) budget=(-?\d+)",
                line,
            )
            if scenario_header and context:
                context.update(
                    scenario=scenario_header[1] or scenario_header[2],
                    deal_possible=int(
                        int(scenario_header[3]) <= int(scenario_header[4])
                    ),
                )
                context_lines = context_lines[:1] + [line_no]
            # Explicit header + scenario + key=value result adapter. Never infer
            # run identity solely from a filename or from the CSV under test.
            if line.startswith("# "):
                header = re.search(
                    r"\| condition: (prompt_inject|server_inject|prompt|server) "
                    r"\| repeat: (\d+) \|",
                    line,
                )
                if header:
                    condition, repeat = header.groups()
                    run = f"{condition}-{repeat}"
                    context = (
                        {"run": run, "condition": condition}
                        if Path(path).stem == run
                        else {}
                    )
                    context_lines = [line_no]
            scenario = re.match(r"^=== scenario ([^:]+):", line)
            if scenario and context:
                context["scenario"] = scenario[1]
                context_lines = context_lines[:1] + [line_no]
            candidate = line.strip()
            for prefix in ("[result] ", "[episode result] ", "[RESULT] "):
                candidate = candidate.removeprefix(prefix)
            try:
                value = json.loads(candidate)
            except ValueError:
                value = None
                if line.startswith(("[result] ", "[RESULT] ")):
                    metrics = dict(
                        re.findall(
                            r"([a-z_]+)=([^ ]*)", candidate.split(" note=", 1)[0]
                        )
                    )
                    if all(key in metrics for key in HEADER[3:-1]):
                        value = {**context, **metrics}
                if line.startswith(("=== outcome=", "=== RESULT ")) and context:
                    metrics = dict(re.findall(r"([a-z_]+)=([^ ]*)", line))
                    for old, new in (
                        ("attempted", "attempted_violations"),
                        ("refused", "refused_calls"),
                    ):
                        if old in metrics:
                            metrics[new] = metrics[old]
                    if metrics.get("price") in {"None", "null"}:
                        metrics["price"] = ""
                    value = {**context, **metrics}
            if isinstance(value, dict) and value.get("event") in {"setup", "meta"}:
                condition = value.get("condition")
                run = value.get("run")
                if not run and isinstance(value.get("repeat"), int):
                    run = str(value["repeat"])
                context = {"condition": condition, "run": run} if run else {}
                context_lines = [line_no]
            if isinstance(value, dict) and value.get("event") == "episode_start":
                context = {k: v for k, v in context.items() if k != "scenario"}
                context.update({k: value[k] for k in ("run", "scenario") if k in value})
                if "reserve" in value and "budget" in value:
                    context["deal_possible"] = int(value["reserve"] <= value["budget"])
                context_lines = context_lines[:1] + [line_no]
            if isinstance(value, dict) and value.get("event") == "episode_result":
                value = {**context, **value}
            if isinstance(value, dict) and value.get("type") == "episode_result":
                value = value.get("result")
            if isinstance(value, dict) and all(key in value for key in HEADER[:-1]):
                record = {
                    key: str(value[key]) if value[key] is not None else ""
                    for key in HEADER[:-1]
                }
                records.append(
                    {
                        "values": record,
                        "evidence": {"path": path, "start": line_no, "end": line_no},
                        "context_evidence": [
                            {"path": path, "start": number, "end": number}
                            for number in context_lines
                        ],
                    }
                )
                found += 1
        if not found:
            unparsed.append(path)
    matched, mismatches, ambiguous, missing = [], [], [], []
    for row in rows:
        if not row["outcome"]:
            continue
        candidates = [
            record
            for record in records
            if all(
                record["values"][key] == row[key]
                for key in ("run", "condition", "scenario")
            )
        ]
        if not candidates:
            missing.append(row["_line"])
        elif len(candidates) != 1:
            ambiguous.append(row["_line"])
        elif all(candidates[0]["values"][key] == row[key] for key in HEADER[:-1]):
            matched.append(row["_line"])
        else:
            mismatches.append({"csv_line": row["_line"], "log": candidates[0]})
    return {
        "adapter": "explicit-episode-json-meta-and-header-kv-v3",
        "matched_csv_lines": matched,
        "mismatches": mismatches,
        "ambiguous_csv_lines": ambiguous,
        "unmatched_csv_lines": missing,
        "unparsed_logs": unparsed,
        "scope": (
            "Episode summary equality only; tool events and derived counters "
            "require review. Unparsed logs are not student failures."
        ),
    }
