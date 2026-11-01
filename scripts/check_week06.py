#!/usr/bin/env python3
"""Structural checks for the week-06 submission. CI runs exactly this.

Usage: python scripts/check_week06.py submissions/<student-id>/week-06

CI cannot run your harness (it needs your login), so it does not run the trade
test. It checks that you ran the current trade test yourself and that it passed:
test-report.json must come from scripts/trade_test_week06.py as it is in this
repository, with every test present and passing. The instructor may rerun the
trade test with the commands in your agent.json.
"""
import ast
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

KIT = Path(__file__).resolve().parent / "trade_test_week06.py"
REQUIRED = {                     # test name -> runs the trade test records
    "seller_card": 1, "seller_opens": 1, "seller_deal": 3, "seller_terminal": 1, "seller_holds_reserve": 3,
    "buyer_deal": 3, "buyer_protocol_deal": 3, "buyer_holds_budget": 3, "buyer_protocol_holds_budget": 3,
}
MIN_REPORT_CHARS = 600


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
    if not str(cfg.get("harness", "")).strip():
        fail('agent.json: "harness" is empty; name what drives your agents and the model')
    if "{port}" not in seller:
        fail('agent.json: seller.cmd must contain {port}')
    if not all(k in buyer for k in ("{url}", "{item}", "{budget}")):
        fail('agent.json: buyer.cmd must contain {url}, {item} and {budget}')
    ok("agent.json has harness, seller.cmd and buyer.cmd")
    return cfg


def check_report_json(sub: Path, cfg):
    p = sub / "test-report.json"
    if not p.is_file():
        fail("test-report.json missing; run python scripts/trade_test_week06.py on your directory")
        return
    rep = json.loads(p.read_text(encoding="utf-8"))
    kit_hash = hashlib.sha256(KIT.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if rep.get("kit_sha256") != kit_hash:
        fail("test-report.json was made by a different version of the trade test; pull and run it again")
        return
    if cfg and (rep.get("seller_cmd") != (cfg.get("seller") or {}).get("cmd")
                or rep.get("buyer_cmd") != (cfg.get("buyer") or {}).get("cmd")):
        fail("test-report.json was made with other commands than the ones in agent.json; run the trade test again")
    runs = Counter(r["test"] for r in rep.get("results", []))
    missing = [f"{t} ({n - runs[t]} missing)" for t, n in REQUIRED.items() if runs[t] < n]
    if missing:
        fail("test-report.json lacks runs: " + ", ".join(missing))
        return
    failed = [f"{r['test']} #{r['run']}: {r['detail']}" for r in rep["results"] if not r["pass"]]
    if failed or not rep.get("passed"):
        fail(f"{len(failed)} trade test run(s) failed, first: {failed[0] if failed else 'passed is false'}")
        return
    ok(f"test-report.json: {len(rep['results'])} runs passed with the current trade test")


def check_logs(sub: Path):
    p = sub / "logs" / "trade-test.log"
    if not p.is_file():
        fail("logs/trade-test.log missing (the trade test writes it)")
        return
    text = p.read_text(encoding="utf-8", errors="ignore")
    if "TASK_STATE_INPUT_REQUIRED" not in text:
        fail("logs/trade-test.log never shows TASK_STATE_INPUT_REQUIRED")
        return
    ok("logs/trade-test.log present")


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
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sub = Path(sys.argv[1])
    if not sub.is_dir():
        print(f"FAIL  {sub} is not a directory")
        sys.exit(1)
    check_source(sub)
    cfg = check_agent_json(sub)
    check_report_json(sub, cfg)
    check_logs(sub)
    check_report_md(sub)
    if fail.count:
        print(f"\n{fail.count} check(s) failed")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
