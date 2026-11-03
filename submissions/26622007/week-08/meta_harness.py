"""The meta-harness: Terra tunes and repairs every hour, Astra explores and grades every twelve.

Terra adjusts: pricing.json's settings a bounded step at a time, the shop's memory (memory/agent/), broken tools, and the
numbers against the Players objective. Astra explores: new strategies, tools and data sources (ledger, forum, own sales)
as hypotheses with small experiments, and grades what Terra did. Both answer to hard_limits.py: a change that moves a
setting out of its bounds or by more than one step, touches a protected file or drops a hard_limits line is undone.

    python meta_harness.py terra | astra     one cycle
    python meta_harness.py run               the loop (run_meta.sh keeps it alive)

A cycle: scorecard -> the model reads the logs and changes this folder -> citations checked, tests run -> commit ->
ten minutes watched -> an hour of trading -> the next cycle judges it. A failed change is committed and then reverted, so
the attempt stays in history. Models come from META_TERRA_MODEL and META_ASTRA_MODEL; if one is refused, the cycle
says so and the shop goes on. The harness commits only under this folder and checks for secrets before each commit,
because the repository's rules forbid both; pushes happen only with META_PUSH=1.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

import httpx

import hard_limits
import scorecard

ROOT = os.path.abspath(os.environ.get("META_DIR") or os.path.dirname(os.path.abspath(__file__)))
MODELS = {"terra": os.environ.get("META_TERRA_MODEL", "gpt-6.1-terra"),
          "astra": os.environ.get("META_ASTRA_MODEL", "gpt-6-astra")}
CODEX_TIMEOUT = int(os.environ.get("META_CODEX_TIMEOUT", 2700))
CANARY_SECONDS = int(os.environ.get("META_CANARY_SECONDS", 600))
RUN_SECONDS = int(os.environ.get("META_RUN_SECONDS", 3600))
ASTRA_EVERY = int(os.environ.get("META_ASTRA_EVERY", 12 * 3600))
KIT_PATTERN = os.environ.get("META_KIT_PATTERN", "market_kit.py join")
CHATGPT_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
ECON_ROUND = 3
NOT_CODE = ("meta/evidence/", "meta/changes.jsonl", "meta/state.json", "meta/last-")
LIVE = (":!logs", ":!memory")
SELLER_FILES = ("seller.py", "shop_memory.py", "shop_stock.py", "harness.py", "catalog.py", "pricing.json", "acp_shop.py")
EVIDENCE = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["ref", "excerpt", "meaning"],
                                       "properties": {k: {"type": "string"} for k in ("ref", "excerpt", "meaning")}}}
COMMON = {"changed": {"type": "boolean"}, "hypothesis": {"type": "string"}, "change_summary": {"type": "string"},
          "target_metric": {"type": "string"}, "expected": {"type": "string"}, "evidence": EVIDENCE}
SCHEMAS = {
    "terra": {"previous_verdict": {"type": "string", "enum": ["none", "keep", "fix", "revert"]}, "previous_reason": {"type": "string"}},
    "astra": {"summary": {"type": "string"},
              "grades": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["change", "grade", "reason"],
                                                    "properties": {"change": {"type": "string"}, "reason": {"type": "string"},
                                                                   "grade": {"type": "string", "enum": ["worked", "no_effect", "harmful", "unclear"]}}}}},
}


def schema_for(kind):
    props = {**COMMON, **SCHEMAS[kind]}
    return {"type": "object", "additionalProperties": False, "required": sorted(props), "properties": props}


def log(msg):
    line = f"{time.strftime('%m-%d %H:%M:%S')} [meta] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(os.path.join(ROOT, "logs", "meta.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def git(*args, check=True):
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:400]}")
    return p.stdout.strip()


def verify_evidence(items, root=None):
    """Problems with the citations: each must be a real line of a real file here holding the quoted text."""
    root, problems = root or ROOT, []
    if not items:
        return ["no evidence cited"]
    for e in items:
        m = re.fullmatch(r"(.+):(\d+)", e.get("ref") or "")
        if not m:
            problems.append(f"{e.get('ref')!r} is not path:line")
            continue
        path = os.path.realpath(os.path.join(root, m.group(1)))
        if not path.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(path):
            problems.append(f"{m.group(1)} is not a file here")
            continue
        n, excerpt = int(m.group(2)), (e.get("excerpt") or "").strip()
        with open(path, encoding="utf-8", errors="replace") as f:
            line = next((x for i, x in enumerate(f, 1) if i == n), None)
        if line is None:
            problems.append(f"{e['ref']}: no such line")
        elif len(excerpt) < 4 or excerpt not in line:
            problems.append(f"{e['ref']}: the line does not contain {excerpt[:60]!r}")
    return problems


def secret_values():
    """Values that must never enter a commit: the secrets file next to this folder."""
    out = []
    for p in (os.path.join(ROOT, "..", ".env"), os.path.join(ROOT, ".env")):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    m = re.match(r"\s*(?:export\s+)?[A-Z_]*(?:KEY|TOKEN|OWNER|SECRET)[A-Z_]*=[\"']?([^\"'\s]{12,})", line)
                    if m:
                        out.append(m.group(1))
        except OSError:
            pass
    return out


def secrets_in_stage():
    diff = git("diff", "--cached", "--no-color")
    found = [n for n, pat in (("private key", r"BEGIN [A-Z ]*PRIVATE KEY"), ("OpenRouter key", r"sk-or-v1-[0-9a-zA-Z]{20,}"))
             if re.search(r"^\+.*" + pat, diff, re.M)]
    found += ["a value from .env" for v in secret_values() if v in diff]
    return found


def stage(with_logs=False):
    git("add", "-A", "--", ".", *(() if with_logs else LIVE))
    return git("diff", "--cached", "--name-only", "--relative").splitlines()


def run_tests():
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"], cwd=ROOT, capture_output=True,
                       text=True, timeout=900)
    return p.returncode == 0, p.stderr[-1500:]


def restart_seller(paths):
    """The hooks start fresh at each use; only a long-running seller needs a restart. The kit's watchdog starts it again."""
    if any(os.path.basename(p) in SELLER_FILES for p in paths) and KIT_PATTERN:
        subprocess.run(["pkill", "-f", "seller.py --port"], check=False)
        log("seller restarted for the change")


def kit_alive():
    return not KIT_PATTERN or subprocess.run(["pgrep", "-f", KIT_PATTERN], capture_output=True).returncode == 0


def canary(seconds=None):
    """Watches the log for the first minutes of a change. Returns the reasons to undo it; none means carry on."""
    seconds = CANARY_SECONDS if seconds is None else seconds
    before = scorecard.log_counts(ROOT)
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(min(30, max(0.01, end - time.time())))
        if not kit_alive():
            return ["the kit is not running"]
    now = scorecard.log_counts(ROOT)
    grew = lambda k: now[k] - before[k]
    bad = [f"{k} +{grew(k)}" for k, limit in (("tracebacks", 3), ("settle_refused", 5), ("checkout_refused", 10),
                                              ("supply_refused", 10), ("harness_failed", 10)) if grew(k) >= limit]
    if KIT_PATTERN and seconds >= 60 and grew("lines") <= 0:
        bad.append("the kit's log did not grow")
    return bad


def version_of(path):
    """(0, 165, 0) from `codex-cli 0.165.0-alpha.1.2`, or None if it does not run."""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(map(int, m.groups())) if m else None


def find_codex():
    """The newest Codex here: an old npm CLI on PATH refuses gpt-6.1-terra (0.153.2 did, 0.165 takes it).
    META_CODEX overrides."""
    if os.environ.get("META_CODEX"):
        return os.environ["META_CODEX"]
    found = [(version_of(p), p) for p in (shutil.which("codex"), CHATGPT_CODEX) if p and os.path.exists(p)]
    found = [f for f in found if f[0]]
    return max(found)[1] if found else None


def call_model(kind, cycle, pack):
    codex = find_codex()
    if not codex:
        return None, "no codex found"
    os.makedirs(os.path.join(ROOT, "meta"), exist_ok=True)
    schema, out = os.path.join(ROOT, "meta", f"last-{kind}-schema.json"), os.path.join(ROOT, "meta", f"last-{kind}-answer.json")
    with open(schema, "w", encoding="utf-8") as f:
        json.dump(schema_for(kind), f)
    if os.path.exists(out):
        os.remove(out)
    with open(os.path.join(ROOT, "meta", f"{kind}.md"), encoding="utf-8") as f:
        prompt = f.read()
    prompt += f"\n\nCycle {cycle}. Evidence pack: {os.path.relpath(pack, ROOT)}. Past changes: meta/changes.jsonl.\n"
    access = os.environ.get("META_CODEX_ACCESS", "--dangerously-bypass-approvals-and-sandbox").split()   # the Mac mini is its own
    cmd = [codex, "exec", "-m", MODELS[kind], *access, "--skip-git-repo-check", "--ephemeral",
           "-C", ROOT, "--output-schema", schema, "-o", out, "-"]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=CODEX_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"no answer within {CODEX_TIMEOUT} s"
    with open(os.path.join(ROOT, "logs", "meta-codex.log"), "a", encoding="utf-8") as f:
        f.write(f"\n=== {cycle} {MODELS[kind]} exit {p.returncode}\n{p.stdout[-20000:]}\n{p.stderr[-4000:]}\n")
    if p.returncode:
        return None, f"codex exit {p.returncode}: {(p.stderr or p.stdout).strip()[-300:]}"
    try:
        with open(out, encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, ValueError) as e:
        return None, f"no structured answer: {type(e).__name__}"


def commit(message):
    git("commit", "-q", "-m", f"{message}\n\nMeta-Harness: {os.path.basename(__file__)}")
    return git("rev-parse", "HEAD")


def undo(sha, why):
    git("revert", "--no-commit", sha)
    if git("diff", "--cached", "--name-only"):
        git("commit", "-q", "-m", f"revert(week-08): {sha[:8]} 되돌림 - {why}"[:300])
    log(f"undid {sha[:8]}: {why}")


def record(rec):
    with open(os.path.join(ROOT, "meta", "changes.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stage()
    if git("diff", "--cached", "--name-only") and not secrets_in_stage():
        commit(f"chore(week-08): 메타 하네스 기록 {rec['cycle']} - {rec['status']}"[:200])


def archive_logs():
    """Astra's commit carries the logs. The kit keeps one rotated file only, so each is saved under its own name first."""
    rot = os.path.join(ROOT, "logs", "market-kit.log.1")
    if os.path.exists(rot):
        keep = os.path.join(ROOT, "logs", time.strftime("market-kit-rotated-%m%d-%H%M.log", time.localtime(os.path.getmtime(rot))))
        if not os.path.exists(keep):
            shutil.copy2(rot, keep)


def pricing_at_head():
    try:
        return json.loads(git("show", "HEAD:./pricing.json"))
    except (RuntimeError, ValueError):
        return {}


def pricing_now():
    try:
        with open(os.path.join(ROOT, "pricing.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        return {"_unreadable": str(e)}


def limit_problems(code):
    """What hard_limits says about the staged change: settings out of bounds or moved too far, protected files."""
    new = pricing_now()
    if "_unreadable" in new:
        return [f"pricing.json is not valid JSON: {new['_unreadable']}"]
    return hard_limits.check_policy_change(pricing_at_head(), new) + \
        hard_limits.guard_problems(code, git("diff", "--cached", "--no-color", "--", *code) if code else "")


def push():
    if os.environ.get("META_PUSH") == "1":
        log(git("push", "origin", f"HEAD:{os.environ.get('META_PUSH_BRANCH', 'week-08')}", check=False) or "pushed")


def cycle(kind):
    cid = f"{kind}-{time.strftime('%m%d-%H%M%S')}"
    os.makedirs(os.path.join(ROOT, "meta", "evidence"), exist_ok=True)
    pack = os.path.join(ROOT, "meta", "evidence", f"{cid}.json")
    with open(pack, "w", encoding="utf-8") as f:
        json.dump(scorecard.build(ROOT), f, ensure_ascii=False, indent=1, default=str)
    log(f"{cid}: asking {MODELS[kind]}")
    answer, err = call_model(kind, cid, pack)
    rec = {"cycle": cid, "model": MODELS[kind], "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "answer": answer}
    if kind == "astra":
        archive_logs()
    paths = stage(with_logs=kind == "astra")
    code = [p for p in paths if not p.startswith(NOT_CODE) and not p.startswith(("logs/", "memory/"))]

    if secrets_in_stage():      # nothing goes into history, the attempt included
        git("reset", "-q")
        subprocess.run(["git", "checkout", "--", *code], cwd=ROOT, check=False)
        for p in code:
            if os.path.exists(os.path.join(ROOT, p)) and git("ls-files", "--", p, check=False) == "":
                os.remove(os.path.join(ROOT, p))
        rec.update(status="discarded_secret", reason="a secret was about to be committed")
        log(f"{cid}: DISCARDED, a secret was in the change")
        record(rec)
        return rec

    problems = [err] if err else []
    if answer is not None:
        problems += verify_evidence(answer.get("evidence"))
    problems += limit_problems(code)
    moved = hard_limits.policy_diff(pricing_at_head(), pricing_now())
    if moved:
        log(f"{cid}: settings moved: {'; '.join(moved)}")
    if code and not problems:
        ok, output = run_tests()
        if not ok:
            problems.append(f"tests failed: {output[-500:]}")
    if err and not code:
        rec.update(status="model_error", reason=err)
        log(f"{cid}: {err}")
        record(rec)
        return rec
    if not code:
        rec.update(status="no_change" if not problems else "no_change_unsupported", problems=problems)
        log(f"{cid}: no change ({len(problems)} citation problems)")
        record(rec)
        return rec

    title = (answer or {}).get("change_summary") or err or "변경"
    if problems:
        sha = commit(f"wip(week-08): [{cid}] 실패 - {title} / {problems[0]}"[:300])
        undo(sha, f"{cid} 검증 실패: {problems[0]}")
        rec.update(status="rejected", problems=problems, commit=sha)
        record(rec)
        return rec

    why = (answer or {}).get("hypothesis") or ""
    body = "\n".join(x for x in (f"이유: {why}" if why else "", ("설정: " + "; ".join(moved)) if moved else "",
                                 f"기대: {(answer or {}).get('target_metric', '')} {(answer or {}).get('expected', '')}".strip()) if x)
    sha = commit(f"feat(week-08): [{cid}] {title}"[:300] + (f"\n\n{body}" if body else ""))
    rec.update(commit=sha, files=code, settings=moved)
    log(f"{cid}: applied {sha[:8]}: {title}")
    restart_seller(code)
    bad = canary()
    if bad:
        undo(sha, f"{cid} 10분 점검 실패: {'; '.join(bad)}")
        restart_seller(code)
        rec.update(status="canary_failed", problems=bad)
    else:
        rec.update(status="running")
        log(f"{cid}: passed the watch")
    record(rec)
    return rec


def market_round():
    try:
        return httpx.get(f"{scorecard.MARKET}/api/stats", timeout=15).json()["round"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def run():
    """Terra, then its hour of trading; Astra when twelve hours have passed. Nothing happens before Round 3: no data."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    last_astra = time.time()
    while True:
        if (market_round() or 0) < ECON_ROUND:
            log("Round 3 has not opened, waiting")
            time.sleep(300)
            continue
        for kind in ("terra", "astra"):
            if kind == "astra" and time.time() - last_astra < ASTRA_EVERY:
                continue
            try:
                cycle(kind)
            except Exception as e:   # noqa: BLE001 - the shop runs on regardless; the next cycle tries again
                log(f"{kind} cycle failed: {type(e).__name__}: {e}")
            if kind == "astra":
                last_astra = time.time()
            push()
        time.sleep(RUN_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("terra", "astra", "run"):
        sys.exit(__doc__)
    if sys.argv[1] == "run":
        run()
    else:
        print(json.dumps(cycle(sys.argv[1]), ensure_ascii=False, indent=1))
