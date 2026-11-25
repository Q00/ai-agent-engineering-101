"""memory.search and outer.memory_eval on a small made-up memory: one question per LongMemEval ability and more.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, paths  # noqa: E402
from outer import memory_eval  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402

DAY = 86400


def finding(claim, *ev):
    return {"claim": claim, "evidence": list(ev) or ["ledger 1"]}


class MemorySearchTest(Base):
    def setUp(self):
        super().setUp()
        m = paths.memory()
        t0 = 1_800_000_000
        atomic.write_json(m / "epochs" / "e3" / "2026-11-04T10" / "market.json", {
            "kind": "market", "epoch": "e3", "slice": "2026-11-04T10", "ts": t0,
            "summary": "Drinks sell fast. kcwarni opened with prices near cost.",
            "findings": [finding("kcwarni asks about 60% of list on G0011e806", "ledger 120")]})
        atomic.write_json(m / "epochs" / "e3" / "2026-11-05T10" / "market.json", {
            "kind": "market", "epoch": "e3", "slice": "2026-11-05T10", "ts": t0 + DAY,
            "summary": "kcwarni raised its prices; buyers moved to elm.",
            "findings": [finding("kcwarni now asks 90% of list", "ledger 480"), finding("elm took kcwarni's buyers", "ledger 490")]})
        atomic.write_json(m / "epochs" / "e3" / "2026-11-05T10" / "self.json", {
            "kind": "self", "epoch": "e3", "slice": "2026-11-05T10", "ts": t0 + DAY,
            "summary": "We lost three food units to expiry; the seller asked too high on kimchi.",
            "findings": [finding("3 food units expired", "eval.losses")]})
        old = {"name": "elm", "updated": t0, "as_shop": {"summary": "slow shop with high asks", "interpreted": []}}
        atomic.write_json(m / "profiles" / "elm.json", {"name": "elm", "updated": t0 + DAY, "changed": True,
                          "as_shop": {"summary": "fast shop, now cheap, certificates always", "interpreted": [
                              finding("cheap since day 2", "ledger 490")]}})
        (m / "profiles" / "elm.history.jsonl").write_text(json.dumps(old) + "\n", encoding="utf-8")
        atomic.write_json(m / "profiles" / "kcwarni.json", {"name": "kcwarni", "updated": t0 + DAY,
                          "as_shop": {"summary": "raised prices on day 2", "interpreted": []}})
        atomic.write_json(m / "strategy" / "proposals" / "2026-11-05T12.json", {
            "proposal_id": "2026-11-05T12", "slice": "2026-11-05T12", "ts": t0 + DAY + 7200, "version_from": 3,
            "score": "shop ALL 40th percentile", "happened": [finding("losses from expired food", "eval.losses")],
            "hypothesis": "H1 holds", "counterparts": "kcwarni raised prices",
            "options": [{"id": "A", "change": {"phase": "profit"}, "expected": "more margin", "risk": "fewer buyers"}]})
        self.t0 = t0

    def ask(self, **args):
        from tools.registry import call
        return call(self.ctx, "memory.search", args)["result"]

    def test_five_abilities(self):
        t0 = self.t0
        qs = [
            {"q": "what did kcwarni ask on G0011e806", "ability": "extraction",
             "expect": ["epochs/e3/2026-11-04T10/market.json"]},
            {"q": "kcwarni prices", "ability": "multi_slice", "filters": {"kind": "market summary"},
             "expect": ["epochs/e3/2026-11-04T10/market.json", "epochs/e3/2026-11-05T10/market.json"]},
            {"q": "kcwarni prices", "ability": "temporal", "filters": {"since": t0 + DAY - 1, "kind": "market summary"},
             "expect": ["epochs/e3/2026-11-05T10/market.json"]},
            {"q": "elm shop prices", "ability": "update", "filters": {"kind": "profile", "participant": "elm"},
             "expect": ["profiles/elm.json"]},
            {"q": "warranty refunds on broken lamps", "ability": "abstention", "expect": []},
        ]
        out = memory_eval.run(qs)
        self.assertEqual(out["right"], out["total"], out["questions"])

    def test_update_hides_old_versions_unless_asked(self):
        r = self.ask(query="elm shop", participant="elm", kind="profile")
        self.assertEqual([x["pointer"] for x in r["records"]], ["profiles/elm.json"])
        self.assertEqual(self.ask(query="elm slow high asks", participant="elm", kind="profile")["found"], 0)
        r = self.ask(query="elm slow high asks", participant="elm", kind="profile", history=True)
        self.assertEqual([(x["pointer"], x["superseded"]) for x in r["records"]], [("profiles/elm.history.jsonl#0", True)])

    def test_keys_and_raw(self):
        r = self.ask(query="expired food losses")
        kinds = {x["kind"] for x in r["records"]}
        self.assertTrue({"self summary", "brief"} <= kinds)
        top = self.ask(query="kcwarni", participant="kcwarni")["records"]
        self.assertTrue(all("kcwarni" in x["participants"] for x in top))
        from tools.registry import call
        raw = call(self.ctx, "memory.raw", {"pointer": "profiles/elm.history.jsonl#0"})["result"]
        self.assertIn("slow shop", raw["lines"][0])
        self.assertFalse(call(self.ctx, "memory.raw", {"pointer": "../../etc/passwd"})["ok"])

    def test_korean_words(self):
        r = self.ask(query="김치 가격")
        self.assertEqual(r["found"], 0)          # nothing in Korean: says so
        m = paths.memory()
        atomic.write_json(m / "epochs" / "e3" / "2026-11-06T10" / "market.json", {
            "kind": "market", "slice": "2026-11-06T10", "ts": self.t0 + 2 * DAY,
            "summary": "포기김치가 가장 많이 팔렸다", "findings": []})
        r = self.ask(query="김치")
        self.assertEqual(r["records"][0]["slice"], "2026-11-06T10")


if __name__ == "__main__":
    unittest.main()
