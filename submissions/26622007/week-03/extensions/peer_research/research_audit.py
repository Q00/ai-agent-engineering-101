"""Research acceptance checks; answer keys never enter worker or tool requests."""
import re

from memory import URL
from web_transport import canonical_url


def evaluate(outcome, expected, context, catalog, events, task, followup):
    artifact = outcome.artifact or {"summary": "", "facts": {}, "evidence": []}
    facts = artifact["facts"]
    current = set(catalog)
    delivered = set().union(*context.delivered.values())
    known = dict(catalog)
    for rows in context.snapshot.values():
        for row in rows:
            if row["id"] in delivered:
                known.update({canonical_url(s["url"]): s for s in row["sources"]})
    cited = {canonical_url(url) for item in artifact["evidence"] for url in URL.findall(item)}
    verified = cited & set(known)
    refs = {v.strip() for v in str(facts.get("memory_refs", "")).split(",") if v.strip() not in ("", "none")}
    evidence_refs = set(re.findall(r"memory:(mem-[a-f0-9]{16})", " ".join(artifact["evidence"])))
    awards = [e for e in events if e["event"] == "award" and e["task_id"] == task.id]
    steps = awards[0]["plan"]["steps"] if awards else []
    independent = {s["id"] for s in steps if not s["depends_on"]}
    joined = [s for s in steps if set(s["depends_on"]) == independent and len(independent) == 2]
    expected_id = "unknown" if followup and not context.enabled else expected["internal_decision_id"]
    expected_owner = "unknown" if followup and not context.enabled else expected["backup_owner"]
    checks = {
        "root_succeeded": outcome.status == "succeeded",
        "required_string_facts": all(isinstance(facts.get(k), str) and facts[k].strip() for k in
            ("recommended_storage", "internal_decision_id", "backup_owner", "measured_latency", "unresolved", "memory_refs")),
        "decision_id": facts.get("internal_decision_id") == expected_id,
        "backup_owner": facts.get("backup_owner") == expected_owner,
        "no_invented_latency": facts.get("measured_latency") == expected["measured_latency"],
        "report_length": 1200 <= len(artifact["summary"]) <= 4000,
        "comparison_table": "|" in artifact["summary"],
        "two_independent_then_join": len(steps) == 3 and len(joined) == 1,
        "verified_url_count": len(verified) >= expected["minimum_verified_urls"],
        "fresh_search_cited": bool(cited & current),
        "search_tool_used": any(e["event"] == "web_usage" and e.get("requested") and e.get("citations") for e in events),
        "no_unretrieved_evidence_urls": cited <= set(known),
        "both_products_sourced": (any("github.com/pgvector/pgvector" in u for u in verified)
                                  and any("qdrant.tech/" in u for u in verified)),
        "memory_refs_delivered": refs <= delivered and refs <= evidence_refs,
    }
    if followup:
        checks["new_constraint"] = facts.get("external_saas_allowed") is False
        checks["memory_used_or_explicitly_absent"] = bool(refs) if context.enabled else facts.get("memory_refs") == "none"
    return {"passed": all(checks.values()), "checks": checks,
            "cited_verified_urls": sorted(verified), "unretrieved_urls": sorted(cited - set(known)),
            "memory_refs": sorted(refs),
            "limits": "Checks trace source retrieval and known scenario facts, not claim entailment, measured performance or statistical reproducibility."}
