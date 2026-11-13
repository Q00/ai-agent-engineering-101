"""Audit live language runs and execute the committed analysis plan."""
import csv
import hashlib
from itertools import combinations
import json
from pathlib import Path
import platform
import re
import sys

import numpy
import scipy
import statsmodels
import korean as ko
import statistics_exact as exact

sys.path.insert(0, str(ko.ROOT / "turn_limit"))
import compare as comparison

HERE, ROOT = ko.HERE, ko.ROOT
SUITE = "korean-deepseek-20260922"


def select(rows, language=None, policy=None, condition=None):
    return [r for r in rows if (language is None or r["language"] == language)
            and (policy is None or r["turn_policy"] == policy)
            and (condition is None or r["condition"] == condition)]


def load_data_and_audit():
    en8 = [{**r, "language": "en", "turn_policy": "8"} for r in comparison.read_rows(ROOT / "results.csv")]
    enu_dir = ROOT / "turn_limit/runs/unlimited-deepseek-20260922"
    enu = [{**r, "language": "en", "turn_policy": "none"} for r in comparison.read_rows(enu_dir / "results.csv")]
    en_before = [{**r, "language": "en", "turn_policy": "8-replay"} for r in comparison.read_rows(enu_dir / "first-eight-replay.csv")]
    ko_dir = HERE / "runs" / SUITE
    kr = comparison.read_rows(ko_dir / "results.csv")
    assert len(kr) == 72
    manifest = json.loads((ko_dir / "manifest.json").read_text())
    assert manifest["format"] == ko.lab.FORMAT and manifest["reader_system"] == ko.lab.READER_SYSTEM
    original_scenarios = json.loads((ROOT / "scenarios.json").read_text())
    assert manifest["scenarios"] == [{**s, "item": ko.ITEMS[s["id"]]} for s in original_scenarios]
    config = json.loads((ROOT / "lab/config.json").read_text()); config["max_http_requests"] = None
    assert config == manifest["config"]
    # Preserve the legacy audit's exact row/event matching, before adding analysis labels.
    legacy = json.loads((enu_dir / "verification.json").read_text())
    for name, digest in legacy["unlimited_artifact_hashes"].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
    fixed = json.loads((ROOT / "lab/runs/html-deepseek-20260922/verification.json").read_text())
    for name, digest in fixed["preserved_artifact_hashes"].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
    comparison.lab, comparison.ext = ko.lab, ko.unlimited
    audit8, _ = comparison.audit(select(kr, policy="8"), manifest, False)
    auditu, ko_before = comparison.audit(select(kr, policy="none"), manifest, True)
    # Reproduce the capped outcomes through the original (translated) eight-turn loop.
    for row in select(kr, policy="8"):
        if row["status"] == "crashed": continue
        events = [json.loads(s) for s in (ROOT / "logs" / (row["run"] + ".jsonl")).read_text().splitlines()]
        replies = iter(e for e in events if str(e["scenario"]) == row["scenario"] and e["event"] in ("message", "reader_output"))
        def call(role, messages, fmt):
            e = next(replies); assert role == e.get("speaker", "reader"); return e["text"]
        result = {"deal_possible": int(row["deal_possible"])}
        sc = next(s for s in manifest["scenarios"] if str(s["id"]) == row["scenario"])
        ko.lab.negotiate(sc, row["condition"], call, lambda *a, **k: None, result)
        for k in ("outcome", "price", "correct", "violation", "turns", "format_errors", "reader_calls"):
            assert str(result[k]) == row[k]
    ko_before = [{**r, "language": "ko", "turn_policy": "8-replay"} for r in ko_before]
    audits = {"ko_8": audit8, "ko_none": auditu, "english_original_artifacts_unchanged": True}
    language_adherence = []
    for run in sorted({r["run"] for r in kr}):
        events = [json.loads(s) for s in (ROOT / "logs" / (run + ".jsonl")).read_text().splitlines()]
        messages = [e for e in events if e["event"] == "message"]
        language_adherence.append({"run": run, "messages": len(messages),
            "messages_with_hangul": sum(bool(re.search("[가-힣]", e["text"])) for e in messages)})
    audits["language_adherence_hangul_presence_only"] = language_adherence
    return en8 + enu + kr, en_before + ko_before, audits


def analyze(rows, prefixes):
    tests = []
    def add(family, name, a, b):
        tests.append({"family": family, "comparison": name, **exact.stratified_test(a, b)})
    for policy in ("8", "none"):
        for c in ko.lab.CONDITIONS:
            add("language", f"{policy}: {c}: ko - en", select(rows, "ko", policy, c), select(rows, "en", policy, c))
    for language in ("en", "ko"):
        for policy in ("8", "none"):
            for a, b in combinations(ko.lab.CONDITIONS, 2):
                add("format", f"{language}: {policy}: {b} - {a}", select(rows, language, policy, b), select(rows, language, policy, a))
        for c in ko.lab.CONDITIONS:
            add("turn_policy", f"{language}: {c}: none - 8", select(rows, language, "none", c), select(rows, language, "8", c))
    assert len(tests) == 24
    exact.adjust(tests)
    paired = []
    for language in ("en", "ko"):
        for c in ko.lab.CONDITIONS:
            before = select(prefixes, language, condition=c)
            after = select(rows, language, "none", c)
            paired.append({"language": language, "condition": c,
                "before_deal": sum(r["outcome"] == "deal" for r in before),
                "after_deal": sum(r["outcome"] == "deal" for r in after), **exact.paired_test(before, after)})
    exact.adjust(paired)
    summaries = []
    for language in ("en", "ko"):
        for policy in ("8", "none"):
            group = select(rows, language, policy)
            for summary in comparison.aggregate(group, policy):
                g = [r for r in group if r["condition"] == summary["condition"]]
                unknown = sum(r.get("status") in ("censored", "crashed") for r in g)
                summary.update(language=language, turn_policy=policy, unknown_final=unknown,
                               possible_correct_upper=summary["correct"] + unknown)
                summaries.append(summary)
    return tests, paired, summaries


def main():
    rows, prefixes, audits = load_data_and_audit()
    tests, paired, summaries = analyze(rows, prefixes)
    out = HERE / "runs" / SUITE
    analysis = {"verification_status": "ANALYZED", "assumptions": "independent exchangeable episodes within four fixed scenarios; historical language batches",
        "alpha": 0.05, "primary_multiplicity": "Holm across all 24 unpaired comparisons", "tests": tests,
        "nested_observation_only": paired, "summary": summaries,
        "versions": {"python": platform.python_version(), "numpy": numpy.__version__, "scipy": scipy.__version__, "statsmodels": statsmodels.__version__},
        "analysis_inputs": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (HERE / "analyze.py", HERE / "statistics_exact.py", HERE / "report.py", HERE / "requirements.txt", HERE / "ANALYSIS_PLAN.md", out / "results.csv", ROOT / "results.csv", ROOT / "turn_limit/runs/unlimited-deepseek-20260922/results.csv")}}
    (out / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n")
    (out / "audit.json").write_text(json.dumps(audits, ensure_ascii=False, indent=2) + "\n")
    comparison.write_csv(out / "summary.csv", summaries)
    comparison.write_csv(out / "paired.csv", paired)
    comparison.write_csv(out / "first-eight-replay.csv", prefixes)
    flat = [{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in t.items()} for t in tests]
    comparison.write_csv(out / "tests.csv", flat)
    write_report(rows, tests, paired, summaries, audits)
    print(json.dumps({"episodes": len(rows), "summary": summaries,
        "significant_after_holm": [t for t in tests if t["significant_holm"]],
        "minimum_p": min(t["p_raw"] for t in tests), "minimum_holm": min(t["p_holm"] for t in tests)}, ensure_ascii=False, indent=2))


def write_report(rows, tests, paired, summaries, audits):
    # Filled out below: reporting stays separate from live sampling.
    from report import write_report as render
    render(HERE, rows, tests, paired, summaries, audits)


if __name__ == "__main__": main()
