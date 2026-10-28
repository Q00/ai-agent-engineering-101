"""Frozen boolean questions, bounded source facts and deterministic decisions.

Boolean answers extracted by a model remain semantic observations, not proofs.
Only facts computed here can bypass the model, within their documented scope.
"""

import ast
import copy
import re

from .storage import digest, validate_refs


def validate_atoms(criterion):
    atoms = criterion.get("assessment", {}).get("atoms", [])
    if not atoms:
        return
    parents = {v["id"] for v in criterion["assessment"]["obligations"]}
    if len({v["id"] for v in atoms}) != len(atoms):
        raise ValueError("Atomic questions need unique IDs")
    for atom in atoms:
        if (
            set(atom) != {"id", "parent", "question", "remedy", "effect", "source"}
            or atom["parent"] not in parents
            or atom["effect"] not in {"core", "correction"}
            or atom["source"]
            not in {
                "semantic",
                "semantic-claim",
                "history",
                "episode-equality",
                "json-tool-arguments",
                "json-tool-results",
                "json-episode-coverage",
                "json-tool-count",
                "json-refusal-count",
                "json-move-count",
            }
            or any(
                not isinstance(atom[k], str) or not atom[k].strip()
                for k in ("id", "question", "remedy")
            )
        ):
            raise ValueError("Invalid atomic question contract")
    if {a["parent"] for a in atoms if a["effect"] == "core"} != parents:
        raise ValueError("Every obligation needs a core atomic question")
    policies = criterion["assessment"].get("policy_questions", [])
    if not isinstance(policies, list) or any(
        not isinstance(q, dict)
        or set(q) != {"policy", "question", "options"}
        or not isinstance(q["policy"], str)
        or not re.fullmatch(r"[a-zA-Z0-9_.-]+", q["policy"])
        or not isinstance(q["question"], str)
        or not q["question"].strip()
        or not isinstance(q["options"], list)
        or len(q["options"]) < 2
        or any(not isinstance(o, str) or not o.strip() for o in q["options"])
        or len(set(q["options"])) != len(q["options"])
        for q in policies
    ):
        raise ValueError("Invalid frozen atomic policy questions")
    if len({q["policy"] for q in policies}) != len(policies):
        raise ValueError("Atomic policy questions need unique policy IDs")


def subset(criterion, ids):
    """A task receives one frozen question; the original criterion stays pinned."""
    result = copy.deepcopy(criterion)
    result["assessment"]["atoms"] = [
        a for a in result["assessment"]["atoms"] if a["id"] in ids
    ]
    if {a["id"] for a in result["assessment"]["atoms"]} != set(ids):
        raise ValueError("Unknown atomic question")
    parents = {a["parent"] for a in result["assessment"]["atoms"]}
    result["assessment"]["obligations"] = [
        o for o in result["assessment"]["obligations"] if o["id"] in parents
    ]
    return result


def schema(criterion, ref, fixed):
    from .assessment import obj, text_schema

    return obj(
        {
            "id": {"type": "string", "enum": [criterion["id"]]},
            "facts": obj(
                {
                    atom["id"]: obj(
                        {
                            "value": {"type": ["boolean", "null"]},
                            "note": text_schema(350),
                            "evidence": {"type": "array", "items": ref},
                            **(
                                {
                                    "claim_quote": text_schema(1000),
                                    "counter_quote": text_schema(1000),
                                }
                                if atom["source"] == "semantic-claim"
                                else {}
                            ),
                        }
                    )
                    for atom in criterion["assessment"]["atoms"]
                    if atom["id"] not in fixed
                }
            ),
        }
    )


def compile_raw(raw, criterion, snapshot):
    """Convert facts to legacy observations using frozen correction templates."""
    from .assessment import decision, unique_refs

    atoms = criterion["assessment"]["atoms"]
    if set(raw) != {"id", "facts"} or raw["id"] != criterion["id"]:
        raise ValueError("Atomic finding needs id and facts only")
    if set(raw["facts"]) != {a["id"] for a in atoms}:
        raise ValueError("Each atomic question must be answered exactly once")
    observations, issues = [], []
    for atom in atoms:
        fact = raw["facts"][atom["id"]]
        if (
            set(fact)
            != (
                {"value", "note", "evidence", "claim_quote", "counter_quote"}
                if atom["source"] == "semantic-claim"
                else {"value", "note", "evidence"}
            )
            or fact["value"] is not None
            and type(fact["value"]) is not bool
            or not isinstance(fact["note"], str)
            or not fact["note"].strip()
        ):
            raise ValueError("Invalid atomic fact; a value is boolean or null")
        validate_refs(fact["evidence"], snapshot)
        if fact["value"] is not None and not fact["evidence"]:
            raise ValueError("A definite atomic fact requires evidence")
        if atom["source"] == "semantic-claim":
            if not all(
                isinstance(fact[k], str) for k in ("claim_quote", "counter_quote")
            ):
                raise ValueError("Claim correction needs literal quote fields")
            if fact["value"] is False:
                if fact["claim_quote"] == fact["counter_quote"]:
                    raise ValueError("A claim cannot be its own counterevidence")
                snippets = [
                    "\n".join(
                        snapshot["files"][r["path"]]["text"].splitlines()[
                            r["start"] - 1 : r["end"]
                        ]
                    )
                    for r in fact["evidence"]
                ]
                for key in ("claim_quote", "counter_quote"):
                    if not fact[key].strip() or not any(
                        fact[key] in text for text in snippets
                    ):
                        raise ValueError(
                            "Contradictions require exact quotes from cited originals"
                        )
        if atom["effect"] == "correction" and fact["value"] is False:
            issues.append(
                {
                    "check": atom["parent"],
                    "kind": "factual-error",
                    "feedback": atom["remedy"]
                    + (
                        " 원문: " + fact["claim_quote"]
                        if atom["source"] == "semantic-claim"
                        else ""
                    ),
                    "evidence": fact["evidence"],
                    "rerun_basis": "records-sufficient",
                }
            )
    for parent in criterion["assessment"]["obligations"]:
        selected = [
            a for a in atoms if a["parent"] == parent["id"] and a["effect"] == "core"
        ]
        facts = [raw["facts"][a["id"]] for a in selected]
        if not selected:
            # A correction-only leaf is validated independently; its parent is
            # scored only after the original core leaves are assembled.
            facts = [
                raw["facts"][a["id"]] for a in atoms if a["parent"] == parent["id"]
            ]
        states = [
            "supported"
            if f["value"] is True
            else "contradicted"
            if f["value"] is False
            else "unknown"
            for f in facts
        ]
        if not selected:
            states = [
                "unknown" if all(f["value"] is None for f in facts) else "supported"
            ]
        status = decision(states) if states else "pass"
        notes = []
        for atom, fact in zip(selected, facts):
            prefix = atom["remedy"] if fact["value"] is False else atom["question"]
            notes.append(f"{atom['id']}: {prefix} — {fact['note']}")
        observations.append(
            {
                "id": parent["id"],
                "status": {
                    "pass": "supported",
                    "fail": "contradicted",
                    "pending": "unknown",
                }[status],
                "note": "\n".join(notes) or "국소적 사실 오류는 별도로 검토했습니다.",
                "evidence": unique_refs([r for f in facts for r in f["evidence"]]),
            }
        )
    return {"id": raw["id"], "observations": observations, "issues": issues}


def compile_facts(raw, criterion, snapshot):
    from .assessment import compile_finding

    result = compile_finding(compile_raw(raw, criterion, snapshot), criterion, snapshot)
    negatives = sorted(
        a["id"]
        for a in criterion["assessment"]["atoms"]
        if raw["facts"][a["id"]]["value"] is False
    )
    if result["severity"] == "material":
        result["revision_key"] = (
            "cause-"
            + digest(
                {
                    "criterion": criterion["id"],
                    "failed_atoms": negatives,
                }
            )[:20]
        )
    return result


def fixed_facts(criterion, snapshot, checks):
    """Never turn an unsupported parser format into a missing student record."""
    from .facts import scenario_ancestry

    result = {}
    for atom in criterion.get("assessment", {}).get("atoms", []):
        if atom["source"] == "history":
            fact = scenario_ancestry(snapshot)
            result[atom["id"]] = {
                "value": {"supported": True, "contradicted": False, "unknown": None}[
                    fact["status"]
                ],
                "note": fact["note"],
                "evidence": fact["evidence"],
            }
        elif atom["source"].startswith("json-"):
            structure = checks.get("json_log_structure", {})
            key = {
                "json-tool-count": "count_tool_calls",
                "json-refusal-count": "count_refused_calls",
                "json-move-count": "count_turns",
                "json-tool-arguments": "arguments_present",
                "json-tool-results": "results_paired",
                "json-episode-coverage": "episodes_closed",
            }[atom["source"]]
            equality = checks.get("log_comparison", {})
            rows_covered = bool(equality.get("matched_csv_lines")) and not any(
                equality.get(k)
                for k in ("mismatches", "unmatched_csv_lines", "ambiguous_csv_lines")
            )
            supported = (
                structure.get("matched_counts", {}).get(key.removeprefix("count_"))
                if key.startswith("count_")
                else structure.get(key)
            )
            if supported is True and (key != "episodes_closed" or rows_covered):
                result[atom["id"]] = {
                    "value": True,
                    "note": (
                        (
                            "JSON 이벤트 집계와 CSV가 같습니다. "
                            "실제 실행의 진실성은 증명하지 않습니다."
                        )
                        if key.startswith("count_")
                        else (
                            "명시적 JSON 호출·응답·에피소드 구조를 확인했습니다. "
                            "실제 실행의 진실성은 증명하지 않습니다."
                        )
                    ),
                    "evidence": [v["evidence"] for v in structure["files"].values()],
                }
        elif atom["source"] == "episode-equality":
            comparison = checks.get("log_comparison", {})
            # Exact matched summaries prove only equality of stored summaries.
            # Missing/ambiguous records and offline rescoring need a narrow review.
            if comparison.get("matched_csv_lines") and not any(
                comparison.get(k)
                for k in ("mismatches", "unmatched_csv_lines", "ambiguous_csv_lines")
            ):
                result[atom["id"]] = {
                    "value": True,
                    "note": "지원되는 에피소드 요약의 모든 필드가 CSV와 일치합니다. "
                    "도구 이벤트나 실행의 진실성에 대한 증명은 아닙니다.",
                    "evidence": [
                        {
                            "path": "results.csv",
                            "start": 1,
                            "end": len(
                                snapshot["files"]["results.csv"]["text"].splitlines()
                            ),
                        }
                    ],
                }
    return result


def source_index(snapshot):
    """Index AST branches as source data; do not execute or certify their guards."""
    result = {}
    for path, doc in sorted(snapshot["files"].items()):
        if not path.endswith(".py") or path.startswith("@") or doc["text"] is None:
            continue
        try:
            tree = ast.parse(doc["text"])
        except (SyntaxError, ValueError):
            continue
        functions = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            branches = []
            for child in ast.walk(node):
                if isinstance(child, ast.If):
                    branches.append(
                        {
                            "condition": ast.get_source_segment(
                                doc["text"], child.test
                            ),
                            "line": child.lineno,
                            "body_lines": [v.lineno for v in child.body],
                            "else_lines": [v.lineno for v in child.orelse],
                        }
                    )
            functions.append(
                {
                    "name": node.name,
                    "start": node.lineno,
                    "end": node.end_lineno,
                    "branches": branches,
                }
            )
        result[path] = functions
    return result


def combine_facts(parts, criterion, fixed=None):
    """Retain counterexamples and missing leaf results across complete file parts."""
    values = {}
    for part in parts:
        for key, fact in part.get("atomic_facts", {}).items():
            values.setdefault(key, []).append(fact)
    result = {}
    priority = {False: 2, None: 1, True: 0}
    for atom in criterion["assessment"]["atoms"]:
        candidates = values.get(atom["id"], [])
        if candidates:
            fact = copy.deepcopy(max(candidates, key=lambda f: priority[f["value"]]))
            fact["evidence"] = [ref for f in candidates for ref in f["evidence"]]
        else:
            fact = {
                "value": None,
                "note": "해당 사실 검사의 유효한 결과가 없습니다.",
                "evidence": [],
            }
        if atom["source"] == "semantic-claim" and not candidates:
            fact.update(claim_quote="", counter_quote="")
        result[atom["id"]] = fact
    result.update(copy.deepcopy(fixed or {}))
    return result
