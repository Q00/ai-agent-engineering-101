"""Code agent: fixes the code problems the strategist files, behind the same kind of gate as params.json.

Every 20 minutes (systemd timer):
  1. watch   a fix deployed in the last hour is checked; if the agents stop working it is rolled back (git)
  2. pick    the oldest open item in approvals/issues.jsonl (at most 4 attempts a day, inside the API budget)
  3. fix     Claude edits a copy of the code in runtime/code-agent/work, with two tools only: a text editor
             confined to that folder and the files below, and a test runner
  4. gate    code checks the result itself: only allowed files changed, every file compiles, the existing tests
             pass (they guard price floors, budget ceilings and signatures), and a smoke run of the restock plan
             and buyer shop choice on the live market files succeeds
  5. ship    files read fresh on every order, restock or review go live at once and are committed to git;
             files that need a shop restart wait for Dana's approval as a patch
Never editable: the kit, keys, envelope.json (the gate's bounds), strategist.py, this file, existing tests.
"""
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "runtime/code-agent/work"
STATE = ROOT / "logs/code-agent-state.json"
ISSUES, PENDING = ROOT / "approvals/issues.jsonl", ROOT / "approvals/pending.jsonl"
PY = sys.executable
HOT = {"buyer_policy.py", "shop_strategy.py", "review.py", "restock.py", "market_buyer.py"}   # read fresh per call
RESTART = {"seller_r3.py", "harness.py"}                                                     # long-running readers
EDITABLE = HOT | RESTART
NEW_TEST = re.compile(r"tests/agent_[a-z0-9_]+\.py")
CONTEXT_FILES = ["shop_strategy.py", "seller_r3.py", "buyer_policy.py", "review.py", "restock.py", "market_buyer.py",
                 "harness.py", "run_buyer.py", "run_shop.py", "agent.json", "params.json", "envelope.json"]
TESTS = ["tests/verify_shop_strategy.py", "tests/verify_buyer_policy.py", "tests/verify_guardrails.py"]
MARKET_SAMPLE = ["catalog.json", "inventory.json", "shop-signals.json", "shops.json", "order.json", "demand.json"]
MODEL, MAX_TURNS, MAX_USD_PER_TRY, TRIES_PER_DAY = "claude-opus-5-5", 30, 3.0, 4
PRICE = {"in": 4.0, "out": 20.0, "cache_write": 5.0, "cache_read": 0.20}   # $/MTok, Claude Opus 5.5


def now():
    return time.strftime("%Y-%m-%d %H:%M KST")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_jsonl(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines() if Path(path).exists() else []:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def append_jsonl(path, row):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def save_state(state):
    tmp = Path(str(STATE) + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(STATE)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def say(text):
    import notify
    notify.send(text)
    append_jsonl(ROOT / "logs/code-agent.jsonl", {"ts": now(), "slack": text})


def spent():
    return sum(r.get("usd", 0) for r in read_jsonl(ROOT / "logs/api-cost.jsonl"))


def key_of(issue):
    return hashlib.sha1((issue.get("ts", "") + issue.get("what", "")).encode()).hexdigest()[:10]


# ---------------------------------------------------------------- 1. watch
def healthy_since(ts):
    """The agents still work after a deploy: services up, the buyer still finishes orders and reaches payment,
    the restock plan and the buyer's shop choice still run on the live files."""
    problems = []
    for unit in ("ax-round3-shop", "ax-round3-buyer", "ax-round3-ollama"):
        if subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True).stdout.strip() != "active":
            problems.append(f"{unit} is not active")
    rows = [r for r in read_jsonl_tail(ROOT / "logs/buyer-decisions.jsonl", 4000) if r.get("ts", 0) > ts]
    finished = sum(r.get("event") == "order_finished" for r in rows)
    ready = sum(r.get("event") == "ready_for_payment" for r in rows)
    if time.time() - ts > 900 and finished == 0:
        problems.append("the buyer finished no order since the deploy")
    if finished >= 6 and ready == 0:
        problems.append(f"the buyer finished {finished} orders but reached payment in none")
    smoke = subprocess.run([PY, "-c", SMOKE, str(ROOT)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    if smoke.returncode != 0:
        problems.append("smoke run failed: " + smoke.stderr.strip().splitlines()[-1][:200] if smoke.stderr.strip() else "smoke run failed")
    return problems


def read_jsonl_tail(path, n):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def watch(state):
    d = state.get("deployed")
    if not d or d.get("verified"):
        return True
    problems = healthy_since(d["ts"])
    if problems:
        for f in d["files"]:
            shutil.copy2(ROOT / "runtime/code-agent/before" / f, ROOT / f)
        git("add", *d["files"])
        git("commit", "-m", f"code-agent: roll back {d['commit'][:8]} ({'; '.join(problems)[:150]})\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
        d.update(verified=True, rolled_back=now(), problems=problems)
        state.setdefault("history", []).append(d)
        state["deployed"] = None
        save_state(state)
        say(f":rotating_light: 코드 에이전트가 방금 고친 코드를 되돌렸어요. 까닭: {'; '.join(problems)[:300]}")
        return False
    if time.time() - d["ts"] > 2700:
        d["verified"] = now()
        state.setdefault("history", []).append(d)
        state["deployed"] = None
        save_state(state)
        say(f":white_check_mark: 45분 동안 문제가 없어서 코드 수정을 확정했어요: {d['summary'][:200]}")
        return True
    return False          # still watching: start no new work


# ---------------------------------------------------------------- 3. fix
SMOKE = r"""
import json, sys, shutil, tempfile
from pathlib import Path
root = Path(sys.argv[1]); sys.path.insert(0, str(root))
import shop_strategy, buyer_policy
md = root / ".market"
with tempfile.TemporaryDirectory() as tmp:
    state = Path(tmp)
    for name in ("catalog.json", "inventory.json", "shop-signals.json"):
        if (md / name).exists():
            shutil.copy(md / name, state / name)
    sig = json.loads((state / "shop-signals.json").read_text()); sig["updated"] = 9e18   # no network in the smoke run
    (state / "shop-signals.json").write_text(json.dumps(sig))
    plan = shop_strategy.restock_plan(state)
    assert isinstance(plan, dict) and isinstance(plan.get("buy"), list), plan
catalog = json.loads((md / "catalog.json").read_text())
order, shops = json.loads((md / "order.json").read_text()), json.loads((md / "shops.json").read_text())
picked = buyer_policy.candidates(shops, order, catalog, buyer_policy.read_json(md / "buyer-public-metadata.json", {}).get("shops", {}), {}, {})
assert isinstance(picked, list)
print("smoke ok", len(plan["buy"]), len(picked))
"""


def prepare():
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "tests").mkdir(parents=True)
    (WORK / ".market").mkdir()
    for name in CONTEXT_FILES + ["notify.py", "market_kit.py", "buyer.py"]:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, WORK / name)
    for t in (ROOT / "tests").glob("*.py"):
        shutil.copy2(t, WORK / "tests" / t.name)
    for name in MARKET_SAMPLE + ["buyer-public-metadata.json"]:
        if (ROOT / ".market" / name).exists():
            shutil.copy2(ROOT / ".market" / name, WORK / ".market" / name)
    (WORK / "logs").mkdir()
    for name in ("buyer-decisions.jsonl", "shop-decisions.jsonl"):
        tail = (ROOT / "logs" / name).read_text(encoding="utf-8").splitlines()[-300:] if (ROOT / "logs" / name).exists() else []
        (WORK / "logs" / name).write_text("\n".join(tail) + "\n", encoding="utf-8")
    log = (ROOT / "logs/market-kit.log").read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    (WORK / "logs/market-kit.log").write_text("\n".join(log) + "\n", encoding="utf-8")


def editable(rel):
    if rel in EDITABLE or (NEW_TEST.fullmatch(rel) and not (ROOT / rel).exists()):
        return True
    return False


def resolve(path):
    p = (WORK / path.lstrip("/")).resolve() if not path.startswith(str(WORK)) else Path(path).resolve()
    if not p.is_relative_to(WORK.resolve()):
        raise ValueError("path is outside the work folder")
    return p, p.relative_to(WORK.resolve()).as_posix()


def run_checks(folder):
    """The gate's own checks; the agent can call them too, but the gate never trusts the agent's word."""
    out, ok = [], True
    for f in sorted(folder.glob("*.py")) + sorted((folder / "tests").glob("*.py")):
        r = subprocess.run([PY, "-m", "py_compile", str(f)], capture_output=True, text=True)
        if r.returncode:
            ok = False
            out.append(f"COMPILE FAIL {f.name}: {r.stderr.strip()[-300:]}")
    env = dict(os.environ, AX_TEST_SOURCE=str(folder))
    # The safety tests check the invariants at the baseline settings (shop_strategy.DEFAULTS), so params.json steps aside.
    params, aside = folder / "params.json", folder / "params.json.aside"
    if params.exists():
        params.replace(aside)
    try:
        for t in TESTS + sorted(p.relative_to(folder).as_posix() for p in (folder / "tests").glob("agent_*.py")):
            r = subprocess.run([PY, str(folder / t)], cwd=folder, env=env, capture_output=True, text=True, timeout=300)
            ok &= r.returncode == 0
            out.append(f"{'PASS' if r.returncode == 0 else 'FAIL'} {t}: " + (r.stdout + r.stderr).strip()[-600:])
    finally:
        if aside.exists():
            aside.replace(params)
    r = subprocess.run([PY, "-c", SMOKE, str(folder)], cwd=folder, capture_output=True, text=True, timeout=120)
    ok &= r.returncode == 0
    out.append(f"{'PASS' if r.returncode == 0 else 'FAIL'} smoke: " + (r.stdout + r.stderr).strip()[-400:])
    return ok, "\n".join(out)


TOOLS = [
    {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool", "max_characters": 60000},
    {"name": "run_checks", "description": "Compile every file, run the existing tests and a smoke run of the restock plan and the "
     "buyer's shop choice on a copy of the live market files. Returns PASS/FAIL lines. Call it before you finish.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
]

SYSTEM = f"""You fix one problem in the code of a student's shop and buyer agents that trade in a live class market \
(Round 3, ends 2026-11-17 23:59 KST). You work in a copy of the code; a separate gate decides whether your change goes live.

Files you may edit: {', '.join(sorted(EDITABLE))}; you may add new tests as tests/agent_<name>.py. Everything else is read-only \
(the kit market_kit.py, params.json, envelope.json, existing tests). The gate rejects any change to other files.
The current folder has the code, a copy of the live market files in .market/, and recent log tails in logs/.

Rules:
- Fix the stated problem with the smallest change that does it. Do not refactor, rename, or touch unrelated code.
- Keep every safety property the tests check: price floors, budget ceilings, capital limits, signature checks, honest grade claims.
- Never add anything untruthful (claims, reviews, posts), anything aimed at manipulating other agents, paid dislikes, or new network endpoints.
- Tunable numbers belong in params.json via shop_strategy.params(); do not hard-code new ones where a param would do.
- If the problem is not a code problem, or you cannot find the cause, change nothing and say so.
- Run run_checks before you finish. Finish with a short plain-Korean summary (2-4 sentences, -요 endings): what was wrong, what you changed."""


def fix(issue, state):
    import anthropic
    client = anthropic.Anthropic()
    prepare()
    before = {p.relative_to(WORK).as_posix(): p.read_bytes() for p in WORK.rglob("*") if p.is_file()}
    messages = [{"role": "user", "content": f"Problem filed by the strategist at {issue.get('ts')}:\n{issue['what']}\n\nEvidence:\n{issue.get('evidence', issue.get('why', ''))}"}]
    usd, summary = 0.0, ""
    for _ in range(MAX_TURNS):
        response = client.messages.create(model=MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
                                          output_config={"effort": "high"}, cache_control={"type": "ephemeral"})
        u = response.usage
        cost = ((u.input_tokens or 0) * PRICE["in"] + (u.output_tokens or 0) * PRICE["out"]
                + (getattr(u, "cache_creation_input_tokens", 0) or 0) * PRICE["cache_write"]
                + (getattr(u, "cache_read_input_tokens", 0) or 0) * PRICE["cache_read"]) / 1e6
        usd += cost
        append_jsonl(ROOT / "logs/api-cost.jsonl", {"ts": time.time(), "who": "code-agent", "model": MODEL, "in": u.input_tokens,
                                                     "out": u.output_tokens, "usd": round(cost, 5)})
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason in ("end_turn", "refusal", "max_tokens"):
            summary = " ".join(b.text for b in response.content if b.type == "text").strip()
            break
        results = []
        for block in (b for b in response.content if b.type == "tool_use"):
            try:
                content = run_tool(block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
            except Exception as exc:
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(exc)[:500], "is_error": True})
        messages.append({"role": "user", "content": results})
        if usd > MAX_USD_PER_TRY:
            summary = f"비용 상한(${MAX_USD_PER_TRY})에 닿아 멈췄어요."
            break
    after = {p.relative_to(WORK).as_posix(): p.read_bytes() for p in WORK.rglob("*") if p.is_file()}
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k) and not k.startswith((".market/", "logs/"))
                     and "__pycache__" not in k and not k.endswith(".aside"))
    return changed, summary, usd


def run_tool(name, args):
    if name == "run_checks":
        return run_checks(WORK)[1]
    if name != "str_replace_based_edit_tool":
        raise ValueError("unknown tool")
    p, rel = resolve(args["path"])
    cmd = args["command"]
    if cmd == "view":
        if p.is_dir():
            return "\n".join(sorted(x.relative_to(WORK).as_posix() for x in p.iterdir() if "__pycache__" not in x.name))
        lines = p.read_text(encoding="utf-8").splitlines()
        a, b = (args.get("view_range") or [1, len(lines)])[:2]
        b = len(lines) if b == -1 else b
        return "\n".join(f"{i}\t{lines[i - 1]}" for i in range(max(1, a), min(len(lines), b) + 1))
    if not editable(rel):
        raise ValueError(f"{rel} is read-only. Editable: {', '.join(sorted(EDITABLE))}, or a new tests/agent_*.py")
    if cmd == "create":
        p.write_text(args["file_text"], encoding="utf-8")
        return f"wrote {rel}"
    text = p.read_text(encoding="utf-8")
    if cmd == "str_replace":
        n = text.count(args["old_str"])
        if n != 1:
            raise ValueError(f"old_str matches {n} times; it must match exactly once")
        p.write_text(text.replace(args["old_str"], args.get("new_str", "")), encoding="utf-8")
        return f"edited {rel}"
    if cmd == "insert":
        lines = text.splitlines(keepends=True)
        lines.insert(int(args["insert_line"]), args["insert_text"] if args["insert_text"].endswith("\n") else args["insert_text"] + "\n")
        p.write_text("".join(lines), encoding="utf-8")
        return f"inserted into {rel}"
    raise ValueError("unknown command")


# ---------------------------------------------------------------- 4-5. gate and ship
def ship(issue, changed, summary, usd, state):
    k = key_of(issue)
    record = {"issue": k, "what": issue["what"][:300], "summary": summary[:600], "changed": changed, "usd": round(usd, 3), "ts": now()}
    if not changed:
        record["result"] = "no change"
        say(f":wrench: 코드 에이전트가 살펴봤지만 고칠 코드가 없다고 판단했어요. {summary[:300]}")
        return record
    bad = [c for c in changed if not editable(c)]
    ok, report = run_checks(WORK)
    if bad or not ok:
        record.update(result="rejected by gate", bad_files=bad, checks=report[-1500:])
        say(f":no_entry: 코드 에이전트의 수정이 문에서 막혀 반영하지 않았어요 ({'허용 안 된 파일 ' + ', '.join(bad) if bad else '시험 실패'}). 가게는 그대로예요.")
        return record
    diff = "".join("".join(difflib.unified_diff(
        (ROOT / c).read_text(encoding="utf-8").splitlines(keepends=True) if (ROOT / c).exists() else [],
        (WORK / c).read_text(encoding="utf-8").splitlines(keepends=True), f"a/{c}", f"b/{c}")) for c in changed)
    patch = ROOT / f"approvals/patches/{k}.diff"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(diff, encoding="utf-8")
    if any(c in RESTART for c in changed):
        append_jsonl(PENDING, {"ts": now(), "what": f"코드 수정 반영 (가게 재시작 필요): {', '.join(changed)}. {summary[:200]}",
                               "why": f"시험을 모두 통과했어요. 수정 내용은 approvals/patches/{k}.diff에 있어요.", "patch": str(patch.relative_to(ROOT))})
        record["result"] = "waiting for approval (restart)"
        say(f":raising_hand: 코드 에이전트가 시험을 통과한 수정을 만들었어요. 가게 재시작이 필요해서 승인을 기다려요. 앱을 열어 Claude에게 답해 주세요. {summary[:250]}")
        return record
    backup = ROOT / "runtime/code-agent/before"
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(parents=True)
    for c in changed:
        (backup / c).parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / c).exists():
            shutil.copy2(ROOT / c, backup / c)
    for c in changed:
        (ROOT / c).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WORK / c, ROOT / c)
    git("add", *changed)
    msg = f"code-agent: {issue['what'][:60]}\n\n{summary[:800]}\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
    git("commit", "-m", msg)
    commit = git("rev-parse", "HEAD").stdout.strip()
    state["deployed"] = {"ts": time.time(), "commit": commit, "files": changed, "summary": summary[:300], "issue": k}
    record.update(result="deployed", commit=commit)
    say(f":wrench: 코드 에이전트가 고쳐서 바로 반영했어요 ({', '.join(changed)}). 45분 동안 지켜보고 문제가 생기면 되돌려요. {summary[:300]}")
    return record


def main():
    for line in (ROOT / "runtime/strategist.env").read_text().splitlines() if (ROOT / "runtime/strategist.env").exists() else []:
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    sys.path.insert(0, str(ROOT))
    state = read_json(STATE, {"done": {}, "history": [], "deployed": None})
    if not watch(state):
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return
    budget = read_json(ROOT / "envelope.json", {}).get("_api_budget_usd", 40)
    today = time.strftime("%Y-%m-%d")
    tries = [h for h in state.get("history", []) if str(h.get("ts", "")).startswith(today)]
    open_issues = [i for i in read_jsonl(ISSUES) if key_of(i) not in state["done"]]
    if not open_issues or len(tries) >= TRIES_PER_DAY or spent() > budget - MAX_USD_PER_TRY:
        return
    issue = open_issues[0]
    changed, summary, usd = fix(issue, state)
    record = ship(issue, changed, summary, usd, state)
    state["done"][key_of(issue)] = record["result"]
    state.setdefault("history", []).append(record)
    save_state(state)
    append_jsonl(ROOT / "logs/code-agent.jsonl", record)
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            import notify
            notify.send(f":warning: 코드 에이전트가 오류로 멈췄어요: {type(exc).__name__}: {str(exc)[:250]}. 가게 코드는 그대로예요.")
        finally:
            raise
