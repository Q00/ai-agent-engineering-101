#!/usr/bin/env python3
"""Structural checks for the week-07 submission. CI runs exactly this.

Usage: python scripts/check_week07.py submissions/<student-id>/week-07 [--github <login>]

CI cannot run your harness (it needs your login), so it does not run the market
test. It checks that you ran the current market test yourself and that it passed:
test-report.json must come from scripts/market_test_week07.py as it is in this
repository, with every test present and passing. The instructor may rerun the
market test with the commands in your agent.json. Private keys (*.pem) must not
be in the submission.

The market check reads the names your kit registered from logs/market-kit.log and
looks them up on the market: a shop and a buyer under that name must exist, linked
to a GitHub account (MARKET_OWNER from the market's join page). With --github (CI
passes the PR author) that account must be yours. Whether they are still running at
the deadline is checked by the instructor on the market, not here.
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

KIT = Path(__file__).resolve().parent / "market_test_week07.py"
REQUIRED = {                     # test name -> runs the market test records
    "seller_card": 1, "seller_opens": 1, "seller_deal": 3, "seller_signed_receipt": 3, "seller_terminal": 1,
    "seller_holds_reserve": 3, "buyer_deal": 3, "buyer_protocol_deal": 3, "buyer_holds_budget": 3,
    "buyer_protocol_holds_budget": 3, "buyer_reads_reviews": 1,
}
MIN_REPORT_CHARS = 600
MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")


def fail(msg: str):
    print(f"FAIL  {msg}")
    fail.count += 1


fail.count = 0


def ok(msg: str):
    print(f"ok    {msg}")


def check_source(sub: Path):
    py = [p for p in sub.rglob("*.py") if "__pycache__" not in p.parts and ".venv" not in p.parts]
    for p in py:
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            fail(f"{p.relative_to(sub)} has a syntax error: {e}")
            return
    other = [p for p in sub.rglob("*") if p.suffix in (".js", ".ts", ".go", ".java", ".cs", ".rs")
             and "node_modules" not in p.parts]
    if not py and not other:
        fail("no source file found (your buyer and seller have to be code you ran)")
        return
    ok(f"{len(py) + len(other)} source file(s)" + (", .py files parse" if py else ""))


def check_agent_json(sub: Path):
    p = sub / "agent.json"
    if not p.is_file():
        fail("agent.json missing")
        return None
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"agent.json is not valid JSON: {e}")
        return None
    seller = (cfg.get("seller") or {}).get("cmd", "")
    buyer = (cfg.get("buyer") or {}).get("cmd", "")
    market_buyer = (cfg.get("market_buyer") or {}).get("cmd", "")
    if not str(cfg.get("harness", "")).strip():
        fail('agent.json: "harness" is empty; name what drives your agents and the model')
    if "{port}" not in seller:
        fail('agent.json: seller.cmd must contain {port}')
    if not all(k in buyer for k in ("{url}", "{item}", "{budget}")):
        fail('agent.json: buyer.cmd must contain {url}, {item} and {budget}')
    if not all(k in market_buyer for k in ("{shops}", "{item}", "{budget}")):
        fail('agent.json: market_buyer.cmd must contain {shops}, {item} and {budget}')
    ok("agent.json has harness, seller.cmd, buyer.cmd and market_buyer.cmd")
    return cfg


def check_report_json(sub: Path, cfg):
    p = sub / "test-report.json"
    if not p.is_file():
        fail("test-report.json missing; run python scripts/market_test_week07.py on your directory")
        return
    rep = json.loads(p.read_text(encoding="utf-8"))
    kit_hash = hashlib.sha256(KIT.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if rep.get("kit_sha256") != kit_hash:
        fail("test-report.json was made by a different version of the market test; pull and run it again")
        return
    if cfg and (rep.get("seller_cmd") != (cfg.get("seller") or {}).get("cmd")
                or rep.get("market_buyer_cmd") != (cfg.get("market_buyer") or {}).get("cmd")):
        fail("test-report.json was made with other commands than the ones in agent.json; run the market test again")
    runs = Counter(r["test"] for r in rep.get("results", []))
    missing = [f"{t} ({n - runs[t]} missing)" for t, n in REQUIRED.items() if runs[t] < n]
    if missing:
        fail("test-report.json lacks runs: " + ", ".join(missing))
        return
    failed = [f"{r['test']} #{r['run']}: {r['detail']}" for r in rep["results"] if not r["pass"]]
    if failed or not rep.get("passed"):
        fail(f"{len(failed)} market test run(s) failed, first: {failed[0] if failed else 'passed is false'}")
        return
    ok(f"test-report.json: {len(rep['results'])} runs passed with the current market test")


def check_logs(sub: Path):
    p = sub / "logs" / "market-test.log"
    if not p.is_file():
        fail("logs/market-test.log missing (the market test writes it)")
    elif "TASK_STATE_INPUT_REQUIRED" not in p.read_text(encoding="utf-8", errors="ignore"):
        fail("logs/market-test.log never shows TASK_STATE_INPUT_REQUIRED")
    else:
        ok("logs/market-test.log present")
    k = sub / "logs" / "market-kit.log"
    if not k.is_file() or "[market] ledger #" not in k.read_text(encoding="utf-8", errors="ignore"):
        fail("logs/market-kit.log missing or without a ledger entry; commit the log of your time in the market")
    else:
        ok("logs/market-kit.log present")


def check_no_keys(sub: Path):
    try:                              # tracked files only: an ignored shop_key.pem on your disk is fine
        out = subprocess.run(["git", "ls-files", "--", "."], cwd=sub, capture_output=True, text=True, check=True).stdout
        keys = [f for f in out.splitlines() if f.endswith(".pem")]
    except (OSError, subprocess.CalledProcessError):
        keys = [str(p.relative_to(sub)) for p in sub.rglob("*.pem")]
    if keys:
        fail(f"private keys in the submission: {', '.join(map(str, keys))}; remove them and add *.pem to .gitignore")
    else:
        ok("no *.pem files")


def check_market(sub: Path, github):
    log = sub / "logs" / "market-kit.log"
    text = log.read_text(encoding="utf-8", errors="ignore") if log.is_file() else ""
    names = {role: re.findall(rf"\[market\] {role} registered as (.+?), round \d+", text) for role in ("shop", "buyer")}
    if not names["shop"] or not names["buyer"]:
        fail("logs/market-kit.log has no '[market] shop registered' and '[market] buyer registered' lines; "
             "commit the kit log from the machine your agents run on")
        return
    shop, buyer = names["shop"][-1], names["buyer"][-1]
    try:
        with urllib.request.urlopen(f"{MARKET}/api/board?round=2", timeout=20) as r:
            board = json.load(r)
    except Exception as e:
        fail(f"cannot read {MARKET}/api/board ({type(e).__name__}); try again later")
        return
    for role, name, rows in (("shop", shop, board["shops"]), ("buyer", buyer, board["buyers"])):
        row = next((x for x in rows if x["name"] == name), None)
        if row is None:
            fail(f"the market has no {role} named {name!r}")
            continue
        owner = (row.get("owner") or {}).get("login")
        if not owner:
            fail(f"{role} {name!r} is not linked to GitHub; join again with MARKET_OWNER=<claim code> from {MARKET}/join")
        elif github and owner.lower() != github.lower():
            fail(f"{role} {name!r} belongs to @{owner}, not @{github}")
        else:
            state = "running now" if row.get("alive") else "not running now"
            ok(f"market: {role} {name!r} of @{owner}, {state}, {row.get('deals', 0)} counted deal(s) in Round 2")


def check_report_md(sub: Path):
    p = sub / "REPORT.md"
    if not p.is_file():
        fail("REPORT.md missing")
        return
    n = len(p.read_text(encoding="utf-8").strip())
    if n < MIN_REPORT_CHARS:
        fail(f"REPORT.md has {n} characters; at least {MIN_REPORT_CHARS}")
        return
    ok(f"REPORT.md present ({n} characters)")


def main():
    args = sys.argv[1:]
    github = None
    if len(args) == 3 and args[1] == "--github":
        github = args[2]
        args = args[:1]
    if len(args) != 1:
        sys.exit(__doc__)
    sub = Path(args[0])
    if not sub.is_dir():
        print(f"FAIL  {sub} is not a directory")
        sys.exit(1)
    check_source(sub)
    cfg = check_agent_json(sub)
    check_report_json(sub, cfg)
    check_logs(sub)
    check_market(sub, github)
    check_no_keys(sub)
    check_report_md(sub)
    if fail.count:
        print(f"\n{fail.count} check(s) failed")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
