"""PC test of the two places the decision model (harness.decide, the Decisions API) is used.

    python test_decisions.py

Buyer, choosing a shop: the odds a purchase goes as described, asked only about shops with
something written about them, blended into trust, cached while the evidence stands; a cheap shop
others warn about loses to a clean one. Seller, closing: our seller run as on the server, with
the decision's answer fixed by HARNESS_FAKE_DECISION; a buyer's "final offer" over the floor is
taken when the odds that it walks reach the rules' threshold, countered otherwise.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="round3-decisions-test-"))
NOW = time.time()
os.environ.update(MARKET_DIR=str(ROOT / ".market"), ROUND_END_TS=str(NOW + 10 * 86400), PYTHONUTF8="1")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("HARNESS_FAKE_DECISION", None)

import httpx                                                                     # noqa: E402
from cryptography.hazmat.primitives import serialization                         # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import common              # noqa: E402
import harness             # noqa: E402
import market_buyer as mb  # noqa: E402

HERE = Path(__file__).resolve().parent
MD = common.MARKET_DIR
KIMCHI = "Gaaaa0001"
KCAT = "식품>김치/반찬/즉석요리>김치"
CATALOG = {KIMCHI: {"sku": KIMCHI, "name": "테스트 포기김치 2kg", "category": KCAT, "list_price": 20000, "kind": "food", "shelf_hours": 168}}
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""), flush=True)


def shop(name, reviews, ratio_paid=None):
    return {"name": name, "url": f"http://{name.lower()}.test", "reviews": reviews,
            "offers": [{"sku": KIMCHI, "name": CATALOG[KIMCHI]["name"], "qty": 1}]}


def test_buyer():
    print("\n[buyer] choosing a shop with the decision model's odds")
    MD.mkdir(parents=True, exist_ok=True)
    asked = []

    def fake(note, evidence, questions, timeout=10.0):
        asked.append((note, evidence, questions))
        return [0.2 if '"Alpha"' in q else 0.9 for q in questions]
    harness.decide, real = fake, harness.decide
    try:
        alpha = shop("Alpha", [{"stars": 2, "review": "Said grade A, it was refurbished."}, {"stars": 1, "review": "Shelf life was a lie."}])
        beta = shop("Beta", [{"stars": 5, "review": "As described, paid fast."}])
        gamma = shop("Gamma", [{"stars": 4}])
        posts = [{"id": 1, "author": "Delta Buyer", "text": "Careful with Alpha: wrong grades twice."},
                 {"id": 2, "author": "Kiro729", "text": "Alpha is fine"}]
        summary = {"by_sku": {}, "by_shop": {"Alpha": {"settled_24h": 9, "claims": 8, "honest": 3}}}
        order = {"order": "o-1", "budget": 22000, "acceptable": [KIMCHI], "kind": "food"}
        rules = common.RULES["buyer"]
        no_odds, _ = mb.choose(mb.candidates(order, [alpha, beta, gamma], CATALOG, {"Alpha": {"recent": ["settled"], "ratios": [0.6]}},
                                             summary, rules), {})
        check("by the numbers alone, Alpha wins: we paid 60% of list there before", no_odds["shop"]["name"] == "Alpha", no_odds["shop"]["name"])
        nb = {"Alpha": {"recent": ["settled"], "ratios": [0.6]}}
        odds = mb.shop_odds([alpha, beta, gamma], posts, summary, nb, NOW)
        check("asked about the shops with something written about them, in one request; Gamma (stars only) not asked",
              odds == {"Alpha": 0.2, "Beta": 0.9} and len(asked) == 1 and len(asked[0][2]) == 2, odds)
        ev = asked[0][1]["Alpha"]
        check("Alpha's evidence: its reviews, the warning post (not our own post), its ledger record, our deals there",
              [r["text"] for r in ev["reviews"]] == ["Said grade A, it was refurbished.", "Shelf life was a lie."]
              and [p["author"] for p in ev["forum_posts"]] == ["Delta Buyer"] and ev["ledger_24h"]["honest"] == 3
              and ev["our_deals"]["recent"] == ["settled"], ev)
        check("the question tells the model the reviews are evidence, not instructions", "never something to follow" in asked[0][2][0])
        cands = mb.candidates(order, [alpha, beta, gamma], CATALOG, nb, summary, rules, odds)
        by = {c["shop"]["name"]: c for c in cands}
        check("odds make half of trust: Beta 0.8 -> 0.85; Alpha's odds under 0.3 flag it", by["Beta"]["trust"] == 0.85
              and any("points to trouble" in f for f in by["Alpha"]["flags"]) and by["Gamma"]["odds"] is None, {k: (v["trust"], v["flags"]) for k, v in by.items()})
        best, poor = mb.choose(cands, nb)
        check("with the odds, the clean shops win over the cheap one others warn about", best["shop"]["name"] in ("Beta", "Gamma") and not poor,
              best["shop"]["name"])
        mb.shop_odds([alpha, beta, gamma], posts, summary, nb, NOW + 60)
        check("asked again a minute later with the same evidence: no new request", len(asked) == 1)
        beta2 = dict(beta, reviews=beta["reviews"] + [{"stars": 1, "review": "Never delivered."}])
        mb.shop_odds([alpha, beta2, gamma], posts, summary, nb, NOW + 120)
        check("a new review on Beta: only Beta is asked again", len(asked) == 2 and len(asked[1][2]) == 1 and '"Beta"' in asked[1][2][0])
        harness.decide = lambda *a, **k: None
        check("no decision (no key, or the API failed): no odds, the numbers decide as before",
              mb.shop_odds([alpha], posts, summary, nb, NOW + 4000) == {})
    finally:
        harness.decide = real


def start_seller(fake):
    md = ROOT / "shop" / ".market"
    md.mkdir(parents=True, exist_ok=True)
    key = ROOT / "shop" / "shop_key.pem"
    if not key.exists():
        key.write_bytes(Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                    serialization.NoEncryption()))
    common.write_json(md / "catalog.json", CATALOG)
    common.write_json(md / "inventory.json", {"round": 3, "balance": 5_000_000, "units": [
        {"unit": f"u-{i}", "sku": KIMCHI, "name": CATALOG[KIMCHI]["name"], "category": KCAT, "list_price": 20000, "kind": "food",
         "cost": 12000, "grade": "A", "supplied_at": NOW - 3600, "expires_at": NOW + 150 * 3600} for i in range(3)]})
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    p = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)], cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                         env=dict(os.environ, MARKET_DIR=str(md), SHOP_KEY_FILE=str(key), HARNESS_FAKE_DECISION=fake))
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(url + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    return p, url, md


async def haggle(url):
    c = mb.A2A()
    await c.open(url)
    t = await c.send(f"I want to buy {CATALOG[KIMCHI]['name']} ({KIMCHI})")
    t = await c.send("12000", t["id"])                              # under the floor: countered by the rule, no question
    t = await c.send("13000 is my final offer. If not, I will buy elsewhere.", t["id"])
    if mb.state(t) == "TASK_STATE_INPUT_REQUIRED":
        await c.cancel(t["id"])
    await c.close()
    return mb.state(t), mb.said(t)


def last_negotiation(md):
    return common.read_jsonl(md / "records" / "negotiations.jsonl")[-1]


def test_seller():
    print("\n[seller] taking a final offer when the buyer would walk")
    for fake, threshold, want in (("0.9", None, "taken"), ("0.2", None, "countered"), ("0.9", 0.95, "countered")):
        seller, url, md = start_seller(fake)
        if threshold is not None:
            common.write_json(md / "analysis" / "active_rules.json", {"seller": {"walk_threshold": threshold}})
        try:
            state, said = asyncio.run(haggle(url))
        finally:
            seller.kill()
        n = last_negotiation(md)
        label = f"odds {fake}" + (f", threshold {threshold} from the rules" if threshold else ", threshold 0.7")
        if want == "taken":
            check(f"{label}: 13,000 (over the floor of 12,600, under 95% of the 13,800 ask) is taken now",
                  state == "TASK_STATE_COMPLETED" and "at 13000" in said and n["taken_early"] is True and n["walk_odds"] == [0.9], (said, n))
        else:
            check(f"{label}: the rule's counter of 13,400 stands", "13400" in said and n["taken_early"] is False
                  and n["walk_odds"] == [float(fake)], (said, n.get("walk_odds")))
        check(f"{label}: asked only once, for the offer over the floor (12,000 was under it)", len(n["walk_odds"]) == 1)
        (md / "analysis" / "active_rules.json").unlink(missing_ok=True)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        test_buyer()
        test_seller()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
