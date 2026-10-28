"""Compile bounded semantic observations into deterministic draft decisions."""

import ast
import copy

from .comments import pending_kind
from .storage import canonical, digest, validate_refs

OBSERVATIONS = ("supported", "contradicted", "unknown")
ISSUES = ("factual-error", "unsupported-claim", "implementation-defect", "improvement")
BONUS_CHECKS = ("beyond_required", "distinct", "implemented", "validated", "meaningful")
BLOCK_LINES = 40


def implementation_anchors(snapshot):
    """Stable implementation identities, separate from variable praise citations."""
    anchors = {}
    for path, doc in sorted(snapshot["files"].items()):
        if (
            path.startswith("@")
            or doc["text"] is None
            or "/logs/" in path
            or path.startswith("logs/")
        ):
            continue
        if "/" in path:
            parts = path.split("/")[:-1]
            for end in range(1, len(parts) + 1):
                if "logs" in parts[:end]:
                    break
                directory = "/".join(parts[:end]) + "/"
                anchors[directory] = {"path": directory, "kind": "component"}
        if not path.endswith(".py"):
            anchors[path] = {
                "path": path,
                "start": 1,
                "end": max(1, len(doc["text"].splitlines())),
                "kind": "artifact",
            }
            for number, line in enumerate(doc["text"].splitlines(), 1):
                if path.endswith(".md") and line.startswith("#"):
                    key = path + "::section-" + digest(line.strip())[:12]
                    if key not in anchors:
                        anchors[key] = {
                            "path": path,
                            "start": number,
                            "end": len(doc["text"].splitlines()),
                            "kind": "section",
                            "heading": line.strip(),
                        }
            continue
        anchors[path] = {
            "path": path,
            "start": 1,
            "end": max(1, len(doc["text"].splitlines())),
            "kind": "module",
        }
        try:
            tree = ast.parse(doc["text"])
        except (SyntaxError, ValueError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                anchors[path + "::" + node.name] = {
                    "path": path,
                    "start": node.lineno,
                    "end": node.end_lineno,
                    "kind": "symbol",
                }
        # Distinct additions inside one function need not be collapsed into one point.
        for start in range(1, len(doc["text"].splitlines()) + 1, BLOCK_LINES):
            anchors[path + f"#block-{(start - 1) // BLOCK_LINES + 1}"] = {
                "path": path,
                "start": start,
                "end": start + BLOCK_LINES - 1,
                "kind": "block",
            }
    return anchors


def evidence_blocks(snapshot):
    blocks = {}
    for path, doc in sorted(snapshot["files"].items()):
        lines = (doc["text"] or "").splitlines()
        for start in range(0, len(lines), BLOCK_LINES):
            key = "b" + digest(path)[:12] + "-" + str(start // BLOCK_LINES + 1)
            if key in blocks:
                raise ValueError("Evidence block ID collision")
            blocks[key] = {
                "path": path,
                "start": start + 1,
                "end": min(start + BLOCK_LINES, len(lines)),
            }
    return blocks


def resolve_blocks(value, blocks):
    if isinstance(value, list):
        return [resolve_blocks(v, blocks) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"block"}:
        if value["block"] not in blocks:
            raise ValueError("Unknown evidence block")
        return copy.deepcopy(blocks[value["block"]])
    return {k: resolve_blocks(v, blocks) for k, v in value.items()}


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def text_schema(limit=500):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def obligations(criterion):
    if "assessment" in criterion:
        return criterion["assessment"]["obligations"]
    return [{"id": "core", "rule": criterion["pass_rule"]}]


def finding_schema(criterion, ref, fixed=None):
    if criterion.get("assessment", {}).get("atoms"):
        from .atomic import schema

        return schema(criterion, ref, fixed or {})
    ids = [
        entry["id"]
        for entry in obligations(criterion)
        if entry["id"] not in (fixed or {})
    ]
    return obj(
        {
            "id": {"type": "string", "enum": [criterion["id"]]},
            "observations": obj(
                {
                    key: obj(
                        {
                            "status": {"type": "string", "enum": list(OBSERVATIONS)},
                            "note": text_schema(),
                            "evidence": {"type": "array", "items": ref},
                        }
                    )
                    for key in ids
                }
            ),
            "issues": {
                "type": "array",
                "items": obj(
                    {
                        "check": {"type": "string", "enum": ids},
                        "kind": {"type": "string", "enum": list(ISSUES)},
                        "evidence": {"type": "array", "items": ref},
                        "feedback": text_schema(),
                        "rerun_basis": {
                            "type": "string",
                            "enum": [
                                "records-sufficient",
                                "behavior-change",
                                "missing-records",
                                "undetermined",
                            ],
                        },
                    }
                ),
            },
        }
    )


def unique_refs(refs):
    values = {canonical(ref): ref for ref in refs}
    return [values[key] for key in sorted(values)]


def resolve_quotes(value, snapshot):
    """Locate exact, unique source quotations; never guess or repair citations."""
    if isinstance(value, list):
        return [resolve_quotes(v, snapshot) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"path", "quote"}:
        path, quote = value["path"], value["quote"]
        if (
            path not in snapshot["files"]
            or not isinstance(quote, str)
            or not quote.strip()
        ):
            raise ValueError("Invalid evidence quotation")
        text = snapshot["files"][path]["text"]
        if text is None:
            raise ValueError("Quotation source is unreadable")
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            raise ValueError(
                "Evidence quotation must match a unique source span exactly"
            )
        return {
            "path": path,
            "start": text.count("\n", 0, start) + 1,
            "end": text.count("\n", 0, start + len(quote) - 1) + 1,
        }
    return {k: resolve_quotes(v, snapshot) for k, v in value.items()}


def decision(statuses):
    return (
        "fail"
        if "contradicted" in statuses
        else "pending"
        if "unknown" in statuses
        else "pass"
    )


def compile_finding(raw, criterion, snapshot):
    """No free model status, severity, point count or revision grouping labels."""
    if "facts" in raw:
        from .atomic import compile_facts

        return compile_facts(raw, criterion, snapshot)
    if set(raw) != {"id", "observations", "issues"} or raw["id"] != criterion["id"]:
        raise ValueError("Invalid observation finding")
    expected = {entry["id"] for entry in obligations(criterion)}
    observed = raw["observations"]
    if isinstance(observed, dict):
        observed = [{"id": key, **observed[key]} for key in sorted(observed)]
    if len(observed) != len(expected) or {v["id"] for v in observed} != expected:
        raise ValueError("Every obligation needs exactly one observation")
    for value in observed:
        if set(value) != {"id", "status", "note", "evidence"}:
            raise ValueError("Invalid observation fields")
        if value["status"] not in OBSERVATIONS or not value["note"].strip():
            raise ValueError("Invalid observation status or explanation")
        validate_refs(value["evidence"], snapshot)
        if value["status"] != "unknown" and not value["evidence"]:
            raise ValueError("Supported/contradicted obligations need evidence")
    for issue in raw["issues"]:
        if set(issue) != {"check", "kind", "evidence", "feedback", "rerun_basis"}:
            raise ValueError("Invalid issue fields")
        if issue["check"] not in expected or issue["kind"] not in ISSUES:
            raise ValueError("Issue must link an assigned obligation and issue kind")
        if not issue["feedback"].strip() or not issue["evidence"]:
            raise ValueError("An actionable issue requires feedback and evidence")
        if issue["rerun_basis"] not in {
            "records-sufficient",
            "behavior-change",
            "missing-records",
            "undetermined",
        }:
            raise ValueError("Invalid issue correction basis")
        validate_refs(issue["evidence"], snapshot)
    status = decision([v["status"] for v in observed])
    corrections = [v for v in raw["issues"] if v["kind"] != "improvement"]
    severity = "material" if status == "fail" or corrections else "none"
    if severity == "none" and raw["issues"]:
        severity = "minor"
    refs = unique_refs([ref for v in observed + raw["issues"] for ref in v["evidence"]])
    problem = [v for v in observed if v["status"] != "supported"]
    messages = [v["feedback"] for v in corrections]
    if status != "pass":
        messages += [v["note"] for v in problem]
    if not messages:
        messages = [v["feedback"] for v in raw["issues"]] or [
            criterion.get("title", criterion["id"]) + ": " + observed[0]["note"]
        ]
    basis = "not-applicable"
    if corrections:
        priority = [
            "behavior-change",
            "missing-records",
            "undetermined",
            "records-sufficient",
        ]
        basis = min((v["rerun_basis"] for v in corrections), key=priority.index)
    elif status != "pass":
        basis = "missing-records" if status == "pending" else "undetermined"
    # One proven cause may be shared across criteria only with the same evidence
    # anchor and issue kind. Different semantic causes are never guessed equal.
    cause = (
        {
            "kind": corrections[0]["kind"],
            "evidence": unique_refs(corrections[0]["evidence"]),
        }
        if corrections
        else {"criterion": criterion["id"], "evidence": refs}
    )
    return {
        "id": criterion["id"],
        "status": status,
        "reason": "\n".join(v["id"] + ": " + v["note"] for v in observed),
        "evidence": refs,
        "feedback": "\n".join(dict.fromkeys(messages)),
        "severity": severity,
        "revision_key": "cause-" + digest(cause)[:20] if severity == "material" else "",
        "rerun_basis": basis,
        "pending_reason": "evidence" if status == "pending" else "",
    }


def discovery_schema(ref, policy, available_paths=None, anchors=None):
    result = {
        "type": "array",
        "items": obj(
            {
                "title": text_schema(160),
                "contribution": text_schema(),
                "criterion_id": {
                    "type": "string",
                    "enum": ["", *[c["id"] for c in policy["explicit_criteria"]]],
                },
                "evidence": {"type": "array", "minItems": 1, "items": ref},
                "supporting_files": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": available_paths or ["no-additional-files"],
                    },
                },
            }
        ),
    }
    if anchors is not None:
        item = result["items"]
        item["properties"]["anchor"] = {
            "type": "string",
            "enum": sorted(anchors) or ["no-implementation-anchor"],
        }
        item["required"].append("anchor")
    return result


def catalog_candidates(raw, policy, snapshot):
    """Pin artifact-anchored candidate IDs before assessing any bonus eligibility."""
    explicit = {c["id"]: c for c in policy["explicit_criteria"]}
    candidates = {}
    anchors = (
        implementation_anchors(snapshot) if any("anchor" in c for c in raw) else {}
    )
    for entry in raw:
        required = {"title", "contribution", "criterion_id", "evidence"}
        if not required <= set(entry) or set(entry) - required - {
            "supporting_files",
            "anchor",
        }:
            raise ValueError("Invalid bonus candidate fields")
        if not entry["title"].strip() or not entry["contribution"].strip():
            raise ValueError("Bonus candidate needs a description")
        named = entry["criterion_id"]
        if named and named not in explicit or not named and not policy["open_ended"]:
            raise ValueError("Bonus candidate outside grader policy")
        validate_refs(entry["evidence"], snapshot)
        if not entry["evidence"] or any(
            r["path"].startswith("@") for r in entry["evidence"]
        ):
            raise ValueError("Bonus discovery needs actual submission artifacts")
        refs = unique_refs(entry["evidence"])
        if any(p not in snapshot["files"] for p in entry.get("supporting_files", [])):
            raise ValueError("Candidate requested an absent artifact")
        anchor = entry.get("anchor")
        if anchor is not None:
            definition = anchors.get(anchor)
            cited = definition and any(
                r["path"].startswith(definition["path"])
                if definition["kind"] == "component"
                else r["path"] == definition["path"]
                and r["start"] <= definition["end"]
                and definition["start"] <= r["end"]
                for r in refs
            )
            requested = definition and any(
                p.startswith(definition["path"])
                if definition["kind"] == "component"
                else p == definition["path"]
                for p in entry.get("supporting_files", [])
            )
            if not definition or not (cited or requested):
                raise ValueError("Bonus anchor needs cited implementation evidence")
        key = (
            "explicit-" + named
            if named
            else "extra-" + digest(anchor if anchor is not None else refs)[:20]
        )
        if key in candidates:
            raise ValueError("Duplicate bonus artifact or named criterion")
        candidates[key] = {
            "id": key,
            **copy.deepcopy(entry),
            "evidence": refs,
            "supporting_files": sorted(set(entry.get("supporting_files", []))),
        }
    for key, criterion in explicit.items():
        cid = "explicit-" + key
        candidates.setdefault(
            cid,
            {
                "id": cid,
                "title": criterion["title"],
                "contribution": criterion["rule"],
                "criterion_id": key,
                "evidence": [],
                "supporting_files": [],
            },
        )
    return [candidates[key] for key in sorted(candidates)]


def bonus_schema(candidates, ref, catalog=None):
    criterion = {
        "id": "bonus",
        "assessment": {"obligations": [{"id": k, "rule": k} for k in BONUS_CHECKS]},
    }
    issues = finding_schema(criterion, ref)["properties"]["issues"]
    return {
        "type": "array",
        "minItems": len(candidates),
        "maxItems": len(candidates),
        "items": obj(
            {
                "id": {"type": "string", "enum": [c["id"] for c in candidates]},
                "checks": obj(
                    {
                        k: {"type": "string", "enum": list(OBSERVATIONS)}
                        for k in BONUS_CHECKS
                    }
                ),
                "duplicate_of": {
                    "type": "string",
                    "enum": ["", *[c["id"] for c in (catalog or candidates)]],
                },
                "validation": text_schema(),
                "limitations": text_schema(),
                "evidence": {"type": "array", "items": ref},
                "issues": issues,
            }
        ),
    }


def compile_bonuses(raw, candidates, snapshot, catalog=None):
    by_id = {c["id"]: c for c in candidates}
    if len(raw) != len(by_id) or {v["id"] for v in raw} != set(by_id):
        raise ValueError("Assess each frozen candidate exactly once")
    bonuses = []
    for value in raw:
        if set(value) != {
            "id",
            "checks",
            "duplicate_of",
            "validation",
            "limitations",
            "evidence",
            "issues",
        }:
            raise ValueError("Invalid bonus assessment")
        if set(value["checks"]) != set(BONUS_CHECKS) or any(
            v not in OBSERVATIONS for v in value["checks"].values()
        ):
            raise ValueError("Invalid bonus eligibility checks")
        if (
            value["duplicate_of"]
            not in {"", *[c["id"] for c in (catalog or candidates)]}
            or value["duplicate_of"] == value["id"]
        ):
            raise ValueError("Invalid duplicate contribution target")
        validate_refs(value["evidence"], snapshot)
        status = decision(list(value["checks"].values()))
        if value["duplicate_of"]:
            status = "fail"
        if status != "pending" and not value["evidence"]:
            raise ValueError("Bonus eligibility needs inspected evidence")
        candidate = by_id[value["id"]]
        issue_criterion = {
            "id": candidate["id"],
            "assessment": {"obligations": [{"id": k, "rule": k} for k in BONUS_CHECKS]},
        }
        issue_finding = (
            compile_finding(
                {
                    "id": candidate["id"],
                    "observations": [
                        {
                            "id": k,
                            "status": "supported",
                            "note": value["validation"],
                            "evidence": value["evidence"],
                        }
                        for k in BONUS_CHECKS
                    ],
                    "issues": value["issues"],
                },
                issue_criterion,
                snapshot,
            )
            if value["issues"]
            else None
        )
        bonuses.append(
            {
                **{
                    k: copy.deepcopy(v)
                    for k, v in candidate.items()
                    if k not in {"supporting_files", "anchor"}
                },
                "status": status,
                "contribution_key": candidate["id"],
                "validation": value["validation"],
                "limitations": value["limitations"],
                "evidence": unique_refs(candidate["evidence"] + value["evidence"]),
                "feedback": issue_finding["feedback"]
                if issue_finding
                else value["validation"]
                if status == "pass"
                else value["limitations"],
                "severity": issue_finding["severity"] if issue_finding else "none",
                "revision_key": issue_finding["revision_key"] if issue_finding else "",
                "rerun_basis": issue_finding["rerun_basis"]
                if issue_finding
                else "not-applicable",
            }
        )
    return sorted(bonuses, key=lambda v: v["id"])


def merge_bonus_parts(parts):
    """Conservative whole-file parts: a counterexample or uncertainty is retained."""
    priority = {"pass": 0, "pending": 1, "fail": 2}
    severity = {"none": 0, "minor": 1, "material": 2, "critical": 3}
    result = copy.deepcopy(
        max(parts, key=lambda v: (priority[v["status"]], severity[v["severity"]]))
    )
    correction = max(parts, key=lambda v: severity[v["severity"]])
    result["severity"] = correction["severity"]
    if correction["severity"] in {"material", "critical"}:
        for key in ("revision_key", "rerun_basis"):
            result[key] = correction[key]
    result["evidence"] = unique_refs([ref for v in parts for ref in v["evidence"]])
    for field in ("validation", "limitations", "feedback"):
        result[field] = "\n".join(dict.fromkeys(v[field] for v in parts))
    return result


def assemble_audits(bundle, review):
    """Audit completion of existing criteria, without inventing execution demands."""
    findings = {v["id"]: v for v in review["items"]}
    findings.update(
        {k: v for k, v in bundle["checks"]["items"].items() if k not in findings}
    )
    groups = bundle["rubric"].get("audit_groups") or {
        area: [i["id"] for i in bundle["rubric"]["items"]]
        for area in ("final_answer", "judge_logic", "verification")
    }
    return {
        area: {
            "status": "insufficient"
            if any(pending_kind(k, findings[k]) not in {"", "policy"} for k in ids)
            else "reviewed",
            "reason": "제출 근거에 대한 항목 검토: "
            + ", ".join(ids)
            + ". reviewed는 검토 완료이며 실행 검증·정답 보증이 아닙니다.",
            "evidence": unique_refs(
                [ref for k in ids for ref in findings[k]["evidence"]]
            ),
        }
        for area, ids in groups.items()
    }
