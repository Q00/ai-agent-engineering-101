"""Verify raw preservation and the report's specific transcript-based claims."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from extract import HERE, ROOT, dataset


def main():
    episodes, points, violations = dataset()
    assert (len(episodes), len(points), len(violations)) == (144, 1348, 12)
    turn_counts = Counter(v["violation_turn"] for v in violations)
    assert turn_counts == {2: 1, 4: 4, 6: 3, 7: 1, 8: 3}
    for filename, key, relative_base in [
        ("lab/runs/html-deepseek-20260922/verification.json", "preserved_artifact_hashes", ROOT.parents[2]),
        ("turn_limit/runs/unlimited-deepseek-20260922/verification.json", "unlimited_artifact_hashes", ROOT.parents[2]),
        ("language/runs/korean-deepseek-20260922/verification.json", "preserved_artifact_hashes", ROOT),
    ]:
        for name, digest in json.loads((ROOT / filename).read_text())[key].items():
            p = Path(name)
            if not p.is_absolute(): p = relative_base / p
            assert hashlib.sha256(p.read_bytes()).hexdigest() == digest
    bykey = {e["row"]["run"] + ":" + e["row"]["scenario"]: e for e in episodes}
    e = bykey["korean-deepseek-20260922-none-structured-01:3"]
    # Failed dependency events deliberately omit the parsed act; consult the raw message.
    assert [p["performative"] for p in e["trace"]] == ["propose", "reject-proposal", "", "accept-proposal"]
    assert json.loads(e["trace"][2]["text"])["performative"] == "accept-proposal"
    assert [p["buyer_price"] for p in e["trace"]] == [30] * 4
    assert [p["seller_price"] for p in e["trace"]] == [None] * 4
    assert [p["recorded_violation"] for p in e["trace"]] == [False, False, False, True]
    assert e["trace"][2]["ok"] is False
    assert json.loads(e["trace"][3]["text"])["content"]["price"] == 40
    for run, sid, n, missing, dependency in [
        ("korean-deepseek-20260922-none-tagged-02", "4", 111, 1, 108),
        ("korean-deepseek-20260922-none-free-01", "3", 32, 0, 31),
    ]:
        e = bykey[run + ":" + sid]
        assert len(e["trace"]) == n and all(p["registered_proposal"] is None for p in e["trace"])
        errors = Counter(p["error"] for p in e["trace"] if p["ok"] is False)
        assert errors["Missing leading performative tag"] == missing
        assert errors["Acceptance without a recorded proposal from the other party"] == dependency
    for run, expected in [
        ("unlimited-deepseek-20260922-structured-01", [60, 55, 50, 45, 40]),
        ("unlimited-deepseek-20260922-structured-03", [55, 48, 44, 41, 40]),
    ]:
        e = bykey[run + ":3"]
        assert [p["registered_proposal"] for p in e["trace"] if p["speaker"] == "seller" and p["registered_proposal"] is not None] == expected
        assert (e["row"]["turns"], e["row"]["price"]) == ("11", "40")
    sources = [ROOT / "results.csv", ROOT / "scenarios.json",
               ROOT / "turn_limit/runs/unlimited-deepseek-20260922/results.csv",
               ROOT / "language/runs/korean-deepseek-20260922/results.csv"]
    sources += sorted({ROOT / "logs" / (e["row"]["run"] + ".jsonl") for e in episodes})
    artifacts = sorted(p for p in HERE.iterdir() if (p.suffix in (".py", ".md", ".csv", ".json", ".png", ".svg") or p.name == ".gitattributes") and p.name != "verification.json")
    report = {"episodes": len(episodes), "messages": len(points), "recorded_violations": len(violations),
              "violation_turn_counts": dict(sorted(turn_counts.items())), "all_original_artifacts_unchanged": True,
              "representative_transcript_claims_verified": True,
              "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
              "analysis_artifact_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}}
    (HERE / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("PASS: 144 episodes, 1348 messages, 12 violations.")
    print("PASS: all English/Korean original artifact hashes unchanged.")
    print("PASS: explicit transcript checks for structured price descent, zero-proposal loops, and 40/30 mismatch.")
    print("Violation terminal turns: 2=1, 4=4, 6=3, 7=1, 8=3.")


if __name__ == "__main__": main()
