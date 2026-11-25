"""The meta-harness: Terra tunes and repairs every hour, Astra explores and grades every twelve.

Terra adjusts: pricing.json's settings a bounded step at a time, the shop's memory (memory/agent/), broken tools, and the
numbers against the Players objective. Astra explores: new strategies, tools and data sources (ledger, forum, own sales)
as hypotheses with small experiments, and grades what Terra did. Strategy comes before dials: Astra compares two or three
directions and writes the chosen one in meta/strategy.md; Terra checks each hour whether it still leads to first place in
book (profit until Patch 3.3) and shifts it only after two cycles point the same way. Both answer to hard_limits.py: a change that moves a
setting out of its bounds or by more than one step, touches a protected file or drops a hard_limits line is undone.

    python meta_harness.py terra | astra     one cycle
    python meta_harness.py run               the loop (run_meta.sh keeps it alive)

A cycle: scorecard -> the model reads the logs and changes this folder -> citations checked, tests run -> commit ->
ten minutes watched -> an hour of trading -> the next cycle judges it. A failed change is committed and then reverted, so
the attempt stays in history. Models come from META_TERRA_MODEL and META_ASTRA_MODEL; if one is refused, the cycle
says so and the shop goes on. The harness commits only under this folder and checks for secrets before each commit,
because the repository's rules forbid both; pushes happen only with META_PUSH=1.
"""
import datetime
import glob
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
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
STARTED = time.time()             # Astra's clock when no cycle is recorded yet
TRIED = os.path.join(ROOT, ".market", "meta_tried.json")   # when each kind last started, kept across restarts
NOT_CODE = ("meta/evidence/", "meta/changes.jsonl", "meta/grades.jsonl", "meta/state.json", "meta/last-",
            "meta/experiments_log.jsonl")
LIVE = ("logs", "memory")         # what the kit and the shop keep writing: never staged by the harness (.gitignore too)
# Runtime switches other jobs turn on and off while a cycle runs (A/B studies): never a cycle's change, never staged, never
# undone. 14d7cee (terra-1109-085220) committed seller_warranty_trial.json, which the warranty A/B had just turned on, and
# a code cycle's undo would have turned the study off (independent review 0738-meta I1). .market/ is ignored as well.
RUNTIME = ("seller_warranty_trial.json", ".market/")
# The studies' on/off files: not the model's to set. One that changes while the model runs refuses the cycle, and is left
# as it is (another job may have set it): the edit is recorded, not committed or put back (independent review I-B).
# Each module's "Switch: .market/<x>.json" is here (test_meta checks it); not .market/ as a whole, whose state files move.
SWITCHES = ("seller_warranty_trial.json", ".market/sol_seller_margin_v2/controller.json", ".market/quote_hold/config.json",
            ".market/seller_concession_trial.json", ".market/seller_reader_trial.json", ".market/seller_a_open.json",
            ".market/seller_floor_margin.json", ".market/seller_goods_margin.json", ".market/seller_market_floor.json", ".market/seller_food_margin.json", ".market/shelf_jev_skip/config.json",
            ".market/shelf_fact/config.json", ".market/warranty_fact/config.json",
            ".market/grade_read/config.json", ".market/restock_fresh_slots.json", ".market/restock_turnover_targets.json", ".market/restock_breadth.json", ".market/restock_breadth_t2.json",
            ".market/restock_food_stockout.json",
            ".market/restock_window_fast.json") + tuple(
    f".market/buyer_{n}.json" for n in ("accept_band", "b3b1", "final_m1", "food_ladder", "food_open30", "food_floor30", "food_shelf", "goods_grade", "goods_ladder", "goods_take30", "goods_risk_sku30", "goods_quote_yield30", "goods_quote_yield", "goods_quote_inquiry30", "moss_first", "our_ask",
                                        "post_market", "quote_deadline", "rank_pg",
                                        "saved_floor", "shop_profile", "short_warranty", "warranty_said"))
SWITCHES += (".market/single_price/config.json", ".market/price_first/config.json")
SWITCHES += (".market/restock_first_slot.json",)  # separate finite transmission-slot study
SWITCHES += (".market/restock_first_slot_default.json",)  # adopted policy belongs to its owner
SWITCHES += (".market/restock_first_stock.json",)  # finite supply allocation trial
SWITCHES += (".market/restock_demand_catchup.json",)  # finite quantity allocation belongs to its owner
SWITCHES += (".market/restock_sellable_slots.json",)  # target-count rule: default since W-20261116-1255, this file a finite study or off
SWITCHES += (".market/market_ref_read.json",)   # market_ref_read.py (지시23 11/10)
SWITCHES += (".market/buyer_margin_cap.json",)  # buyer_margin_cap.py: the owner's limit policy (Patch 3.6 Saved)
SWITCHES += (".market/buyer_next_quote.json",)  # buyer_next_quote.py: the next open quote after a failed talk


# A study's root/"adopted" forces arm B and root/"disabled" stops it (late_concession, opening_cue, at_floor_exact, ...):
# switches as well, made or removed (independent review 0738-isolate I).
SWITCH_MARKERS = ("adopted", "disabled")


def runtime(p):
    return p in RUNTIME or p.startswith(tuple(r for r in RUNTIME if r.endswith("/")))


RESTORE = ("pricing.json",)      # Terra's own file that another job may also edit: an undo puts back the cycle's start
SELLER_FILES = ("seller.py", "shop_memory.py", "shop_stock.py", "harness.py", "catalog.py", "pricing.json", "acp_shop.py")
EVIDENCE = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["ref", "excerpt", "meaning"],
                                       "properties": {k: {"type": "string"} for k in ("ref", "excerpt", "meaning")}}}
COMMON = {"changed": {"type": "boolean"}, "hypothesis": {"type": "string"}, "change_summary": {"type": "string"},
          "target_metric": {"type": "string"}, "expected": {"type": "string"}, "evidence": EVIDENCE,
          # "hard to tell" is not an answer: what is missing, the measurement taken for it this cycle, the line that judges it
          "evidence_gap": {"type": "object", "additionalProperties": False, "required": ["missing", "measurement", "judge_line"],
                           "properties": {"missing": {"type": "string"}, "measurement": {"type": "string"},
                                          "judge_line": {"type": "object", "additionalProperties": False,
                                                         "required": ["metric", "direction", "threshold", "min_sample", "expires", "undo"],
                                                         "properties": {"metric": {"type": "string"},
                                                                        "direction": {"type": "string", "enum": ["above", "below", "none"]},
                                                                        "threshold": {"type": "string"}, "min_sample": {"type": "string"},
                                                                        "expires": {"type": "string"}, "undo": {"type": "string"}}}}}}
JUDGE_FIELDS = ("metric", "threshold", "min_sample", "expires", "undo")   # a judge line: the number, which way, how many, by when, how to undo
STRATEGY = "meta/strategy.md"     # the strategy Terra follows: Astra sets it, Terra shifts it only on repeated evidence
EXPERIMENTS = "meta/experiments.json"        # the queue of evidenced experiments: Astra, Terra or a person adds to it
EXPERIMENT_LOG = "meta/experiments_log.jsonl"  # the harness's record of each start and judgment: which one runs
DOCS_ONLY = (STRATEGY, EXPERIMENTS)  # a change of these alone needs no tests and no watch: nothing running reads them


def strict(props):
    return {"type": "object", "additionalProperties": False, "required": sorted(props), "properties": props}


S = {"type": "string"}
SCHEMAS = {
    "terra": {"previous_verdict": {"type": "string", "enum": ["none", "keep", "fix", "revert"]}, "previous_reason": S,
              "grade_summary": S, "weakest": {"type": "array", "items": S},
              "strategy_check": strict({"current": S, "gap_cause": {"type": "string", "enum": ["dials", "strategy", "bug", "unclear"]},
                                        "keep_or_shift": {"type": "string", "enum": ["keep", "watch", "shift"]}, "reason": S}),
              "action_kind": {"type": "string", "enum": ["none", "settings", "tool_new", "tool_fix", "prompt", "code", "memory",
                                                         "strategy_shift"]},
              "experiment": strict({"id": S, "step": {"type": "string", "enum": ["none", "start", "continue", "done", "drop"]},
                                    "result": S})},
    "astra": {"summary": S,
              "strategy": strict({"options": {"type": "array", "items": strict({"direction": S, "expected_profit": S, "risk": S, "test": S})},
                                  "chosen": S, "experiment": S}),
              "grades": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["change", "grade", "reason"],
                                                    "properties": {"change": {"type": "string"}, "reason": {"type": "string"},
                                                                   "grade": {"type": "string", "enum": ["worked", "no_effect", "harmful", "unclear"]}}}},
              "keep_or_drop": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["item", "decision", "reason"],
                                                          "properties": {"item": {"type": "string"}, "reason": {"type": "string"},
                                                                         "decision": {"type": "string", "enum": ["keep", "drop"]}}}}},
}


def schema_for(kind):
    return strict({**COMMON, **SCHEMAS[kind]})


def log(msg):
    line = f"{time.strftime('%m-%d %H:%M:%S')} [meta] {msg}"
    print(line, flush=True)
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(os.path.join(ROOT, "logs", "meta.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def git(*args, check=True):
    # literal pathspecs: a file name with * ? [ or a leading : names that file only, never another job's
    p = subprocess.run(["git", "--literal-pathspecs", *args], cwd=ROOT, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[:400]}")
    return p.stdout.strip()


def holds(line, excerpt):
    """The line holds the quote. A quote under 4 characters only as the whole line (unittest's "OK": 11/08 12 cycles
    were reverted for it)."""
    return bool(excerpt) and excerpt in line and (len(excerpt) >= 4 or excerpt == line.strip())


def cited_file(ref, root):
    """(path here, line number) of `path:line`, or None when it is not that or not a file under root."""
    m = re.fullmatch(r"(.+):(\d+)", ref or "")
    if not m:
        return None
    path = os.path.realpath(os.path.join(root, m.group(1)))
    if not path.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(path):
        return None
    return path, int(m.group(2))


def outside_meta(ref, root=None):
    """The ref names a file here that is not under meta/, judged on the resolved path (cited_file), not the ref's text:
    ./meta/, logs/../meta/ or an absolute path passed a startswith("meta/") test (independent review 0738-meta I2)."""
    root = root or ROOT
    found = cited_file(str(ref or ""), root)
    if not found:
        return False
    # casefold: on the Mac's case-insensitive disk Meta/terra.md is meta/terra.md and realpath keeps the case given
    return os.path.relpath(found[0], os.path.realpath(root)).split(os.sep)[0].casefold() != "meta"


def anchor_evidence(items, root=None):
    """Moves a citation whose quote is not on its line to the nearest line of the same file that holds it, as the file is
    now (the model ends; its test log is complete). The model wrote the number while the log was still growing or from
    a run before: 24 cycles were refused for a real quote at the wrong line (GPT Pro 11/09 07:38 9-1, "OK" at :1577,
    "Ran 1180 tests" at :1654). The quote itself is never changed and verify_evidence still checks every line, so a quote
    the file does not hold is refused as before. Returns "old -> new" for each one moved."""
    root, notes = root or ROOT, []
    for e in items or []:
        found, excerpt = cited_file(e.get("ref"), root), (e.get("excerpt") or "").strip()
        if not found or not excerpt:
            continue
        path, n = found
        best = None
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if best is not None and i - n > abs(best - n):
                    break               # every line from here is farther than the one found
                if holds(line, excerpt):
                    if i == n:          # the cited line holds it after all: leave it
                        best = None
                        break
                    if best is None or abs(i - n) < abs(best - n):
                        best = i
        if best is not None:
            old = e["ref"]
            e["ref"] = f"{old.rsplit(':', 1)[0]}:{best}"
            notes.append(f"{old} -> {e['ref']}")
    return notes


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
        if not cited_file(e["ref"], root):
            problems.append(f"{m.group(1)} is not a file here")
            continue
        path, n = cited_file(e["ref"], root)
        excerpt = (e.get("excerpt") or "").strip()
        with open(path, encoding="utf-8", errors="replace") as f:
            line = next((x for i, x in enumerate(f, 1) if i == n), None)
        if line is None:
            problems.append(f"{e['ref']}: no such line")
        elif not holds(line, excerpt):
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


def secrets_in_stage(paths=None):
    if paths is not None and not paths:      # nothing of the cycle's: another job's staged file is not read
        return []
    diff = git("diff", "--cached", "--no-color", *(["--", *paths] if paths is not None else []))
    found = [n for n, pat in (("private key", r"BEGIN [A-Z ]*PRIVATE KEY"), ("OpenRouter key", r"sk-or-v1-[0-9a-zA-Z]{20,}"))
             if re.search(r"^\+.*" + pat, diff, re.M)]
    found += ["a value from .env" for v in secret_values() if v in diff]
    return found


def dirty_paths():
    """{path: sha1 of its bytes, None when deleted} for each file of this folder that differs from HEAD or is untracked
    and not ignored, logs/ and memory/ left out."""
    names = git("ls-files", "-z", "-m", "-o", "-d", "--exclude-standard", "--", ".").split("\0")
    names += git("diff", "--cached", "--name-only", "-z", "--relative", check=False).split("\0")
    out = {}
    for p in names:
        if not p or p.split("/", 1)[0] in LIVE or runtime(p):
            continue
        full = os.path.join(ROOT, p)
        try:
            data = os.readlink(full).encode() if os.path.islink(full) else open(full, "rb").read()
            out[p] = hashlib.sha1(data).hexdigest()
        except OSError:
            out[p] = None
    return out


def stage(before=None):
    """This folder's changes without logs/ and memory/. Astra's commit eba7dd67 carried them: 64 MB of kit logs, a Round 2
    archive and the shop's memory dumps, which the kit kept writing, so the undo of that commit was refused.
    With `before` (dirty_paths() at the cycle's start) only what changed since then: other work left in the folder is
    not the cycle's. terra-1109-051538 changed nothing and was judged on pricing.json.bak-0406-terra-reoff, another job's
    file (GPT Pro 11/09 07:38 9-2). A file both touched is the cycle's, with the earlier edit in it."""
    if before is not None:
        now = dirty_paths()
        owned = sorted(p for p in now if p not in before or before[p] != now[p])
        if owned:
            git("add", "-A", "--", *owned)
        return owned
    # not `add -A -- . :!logs :!memory`: with both in .gitignore git exits 1 on those pathspecs ("paths are ignored"),
    # which failed every Terra cycle from 11/04 11:07 (f0a8917) and left its change staged, uncommitted
    git("add", "-A", "--", ".")
    git("reset", "-q", "--", *LIVE, check=False)      # tracked evidence there stays as committed; no HEAD yet: nothing to undo
    switches = [p for p in git("diff", "--cached", "--name-only", "--relative").splitlines() if runtime(p)]
    if switches:
        git("reset", "-q", "--", *switches, check=False)
    paths = git("diff", "--cached", "--name-only", "--relative").splitlines()
    live = [p for p in paths if p.split("/", 1)[0] in LIVE]
    if live:
        raise RuntimeError(f"stage: {live[:5]} under {LIVE} still staged")
    return paths


LIVE_ENV = ("SHOP_MARKET_NAME", "SHOP_MEMORY", "AGENT_MEMORY_DIR")    # these point shop_memory/agent_memory at live files


def test_env():
    """The environment for the tests and for Terra/Astra: without the market name, so a test writes its scratch memory
    instead of memory/seller_memory_r3.json (11/04 22:06, 03:59 and 06:00 runs added 72 test talks there and failed 5
    tests at any HEAD), and with this venv first on PATH (the system python3 has no httpx)."""
    env = {k: v for k, v in os.environ.items() if k not in LIVE_ENV}
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    return env


# The seller study attests that its modules were loaded fresh from this folder (sol_source_load), installed before any
# local import as the safe runner's sitecustomize does. Plain discover imports them first, so ~70 study tests fail with
# fresh_frozen_load_proof and every code change was refused (terra-1109-085220, 11/09 09:03; the 0738 "73 old failures").
TEST_BOOT = ("import sol_source_load, unittest; sol_source_load.install(); "
             "unittest.main(module=None, argv=['python -m unittest', 'discover', '-p', 'test_*.py'])")


def test_command():
    """The tests as the harness and the model run them: with the loader first when this folder has it."""
    if os.path.isfile(os.path.join(ROOT, "sol_source_load.py")):
        return [sys.executable, "-c", TEST_BOOT]
    return [sys.executable, "-m", "unittest", "discover", "-p", "test_*.py"]


def test_prompt():
    cmd = test_command()
    shown = f'{cmd[0]} -c "{cmd[2]}"' if cmd[1] == "-c" else f"{sys.executable} -m unittest discover -p 'test_*.py'"
    return f"Tests: `{shown}` in this folder, the same command and environment the harness uses (the system python3 " \
           "lacks httpx). The harness runs them on a copy of this folder as you leave it, without .market/, memory/ " \
           "and logs/, which the kit keeps writing.\n"


# What the kit and the shop keep writing, and keys. test_seller_warranty_trial hashes its folder's .market/ and memory/
# before and after each test; run in the live checkout, the kit's writes ERRORed it and every code change was refused
# (terra-1109-085220 09:03, terra-1109-100754 10:07; a clean clone passes). The copy is the working tree, so the
# cycle's own uncommitted and new files are what is tested. It sits where the folder sits in the repo, with the repo's
# own files and scripts/ (market_kit) around it and the tracked files of memory/ and logs/ in it: a bare copy failed 8
# tests whatever the change (independent review 0738-isolate C).
TEST_COPY_LIVE = (".market", "memory", "logs")                        # at the folder's top only
TEST_COPY_NEVER = shutil.ignore_patterns("__pycache__", ".env*", "*.pem")
# META_DIR also selects import-time paths (TRIED): tests must derive them from the copy, not the parent.
# Keep it out of test_env(), which is also used by the model working in the real cycle folder.
TEST_COPY_ENV = ("MARKET_DIR", "SHOP_KEY_PEM", "BUYER_MEMORY", "SHOP_PRICING", "META_DIR")


def tracked(cwd, *paths):
    p = subprocess.run(["git", "ls-files", "-z", "--", *paths], cwd=cwd, capture_output=True, text=True)
    return [x for x in p.stdout.split("\0") if x] if p.returncode == 0 else []


def test_copy(scratch):
    """The folder as the cycle left it, inside a copy of the repo's top files and scripts/; returns the folder's copy."""
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, text=True)
    top = os.path.realpath(p.stdout.strip()) if p.returncode == 0 else os.path.realpath(ROOT)
    copy = os.path.join(scratch, os.path.relpath(os.path.realpath(ROOT), top))
    def skip(d, names):
        live = set(TEST_COPY_LIVE) if os.path.realpath(d) == os.path.realpath(ROOT) else set()
        return live | TEST_COPY_NEVER(d, names)
    shutil.copytree(ROOT, copy, ignore=skip, ignore_dangling_symlinks=True,         # links copied as content, not links
                    dirs_exist_ok=True)                                         # the folder may be the repo's top
    files = [(os.path.join(ROOT, r), os.path.join(copy, r)) for r in tracked(ROOT, *TEST_COPY_LIVE[1:])]
    if top != os.path.realpath(ROOT):
        files += [(os.path.join(top, r), os.path.join(scratch, r)) for r in tracked(top, "scripts")
                  + [r for r in tracked(top, ":(top,glob)*") if "/" not in r]]
    for src, dst in files:
        if os.path.isfile(src) and not TEST_COPY_NEVER("", [os.path.basename(src)]):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
    return copy


def kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):                         # already gone
        pass


def run_tests():
    cmd = test_command()
    if "discover" not in " ".join(cmd):     # the whole suite, never a chosen part of it
        raise RuntimeError("the harness runs the whole test suite")
    env = {k: v for k, v in test_env().items() if k not in TEST_COPY_ENV}
    scratch = tempfile.mkdtemp(prefix="meta-tests-")
    try:
        proc = subprocess.Popen(cmd, cwd=test_copy(scratch), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=env, start_new_session=True)
        try:
            _, err = proc.communicate(timeout=900)
        except subprocess.TimeoutExpired:
            kill_group(proc)                                               # with any process a test started
            try:
                _, err = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:                              # a child left holding the pipe
                err = ""
            return False, "ERROR: the test suite ran over 900 s\n" + (err or "")[-1200:]
        except BaseException:
            kill_group(proc)
            raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    failed = [ln for ln in err.splitlines() if ln.startswith(("FAIL: ", "ERROR: "))]
    return proc.returncode == 0, "\n".join(failed[:20] + [err[-1200:]])


def restart_pricing_snapshot(before, found):
    """A clean, stable tracked version only; dirty or inconsistent ownership never gets the exception."""
    if "pricing.json" in before or "pricing.json" in found:
        return None, None
    try:
        path = os.path.join(ROOT, "pricing.json")
        if os.path.islink(path):
            return None, None
        with open(path, "rb") as f:
            start = os.fstat(f.fileno())
            raw = f.read()
        pricing = json.loads(raw)
        revision = git("rev-parse", "HEAD")
        expected = git("rev-parse", f"{revision}:./pricing.json")
        actual = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if actual != expected or stamp(start) != stamp(os.stat(path)):
            return None, None
        return pricing, revision
    except (OSError, ValueError, RecursionError, RuntimeError):
        return None, None


def seller_pricing_unchanged(before, after):
    """Only valid restock target counts may differ; unknown input never suppresses a restart."""
    reduced, target_keys = [], []
    for cfg in (before, after):
        if type(cfg) is not dict or type(cfg.get("restock")) is not dict:
            return False
        targets = cfg["restock"].get("sku_target_units")
        if type(targets) is not dict or any(type(k) is not str or type(v) is not int or v < 0
                                           for k, v in targets.items()):
            return False
        target_keys.append(set(targets))
        restock_cfg = dict(cfg["restock"])
        restock_cfg.pop("sku_target_units")
        other = {**cfg, "restock": restock_cfg}
        try:
            reduced.append(json.dumps(other, sort_keys=True, separators=(",", ":"), allow_nan=False))
        except (TypeError, ValueError, RecursionError):
            return False
    return target_keys[0] == target_keys[1] and reduced[0] == reduced[1]


def restart_seller(paths, *, pricing_before=None):
    """Keep talks for target-only settings; other seller changes retain the existing restart."""
    seller_paths = [p for p in paths if os.path.basename(p) in SELLER_FILES]
    if not seller_paths or not KIT_PATTERN:
        return
    if seller_paths == ["pricing.json"] and seller_pricing_unchanged(pricing_before, pricing_now()):
        log("seller kept running: only restock target counts or unchanged settings")
        return
    subprocess.run(["pkill", "-f", "seller.py --port"], check=False)
    log("seller restarted for the change")


def kit_alive():
    return not KIT_PATTERN or subprocess.run(["pgrep", "-f", KIT_PATTERN], capture_output=True).returncode == 0


def kit_log_id(name="market-kit.log"):
    try:
        return os.stat(os.path.join(ROOT, "logs", name)).st_ino
    except OSError:
        return None


def kit_log_counts():
    """The kit log's counts and the file they came from, read again if the kit moved the log during the read."""
    while True:
        log_id = kit_log_id()
        counts = scorecard.log_counts(ROOT)
        if kit_log_id() == log_id:
            return log_id, counts


def kit_log_counts_since(log_id):
    """The kit log's counts now, plus the part the kit moved to market-kit.log.1 at 20 MB when that is the file that was
    market-kit.log (log_id). 11/04 09:27 it moved during Astra's watch: the count fell from ~171,000 lines to 872 and the
    watch undid a sound change for "the kit's log did not grow". Two moves inside one watch lose the first part."""
    while True:
        moved = log_id is not None and kit_log_id("market-kit.log.1") == log_id
        now_id, now = kit_log_counts()
        if moved:
            old = scorecard.log_counts(ROOT, "market-kit.log.1")
            return {k: now[k] + old.get(k, 0) for k in now}
        if log_id is None or now_id == log_id or kit_log_id("market-kit.log.1") != log_id:
            return now                # not moved, or moved too long ago to find: read as is


def canary(seconds=None):
    """Watches the log for the first minutes of a change. Returns the reasons to undo it; none means carry on."""
    seconds = CANARY_SECONDS if seconds is None else seconds
    log_id, before = kit_log_counts()
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(min(30, max(0.01, end - time.time())))
        if not kit_alive():
            return ["the kit is not running"]
    now = kit_log_counts_since(log_id)
    if now.get("tracebacks_open"):     # a block half written at the read: let it finish before counting it
        time.sleep(2)
        now = kit_log_counts_since(log_id)
    # a traceback block still open at the end counts as new; one open at the start is not taken off when it turns out noise
    now = dict(now, tracebacks=now["tracebacks"] + now.get("tracebacks_open", 0))
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
    prompt += test_prompt()
    access = os.environ.get("META_CODEX_ACCESS", "--dangerously-bypass-approvals-and-sandbox").split()   # the Mac mini is its own
    cmd = [codex, "exec", "-m", MODELS[kind], *access, "--skip-git-repo-check", "--ephemeral",
           "-C", ROOT, "--output-schema", schema, "-o", out, "-"]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=CODEX_TIMEOUT, env=test_env())
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


def commit(message, paths=None):
    """With paths, those alone: what someone else staged stays staged and out of the harness's commit. A path with
    nothing staged (a file another job staged new and the cycle deleted) is left out, not a pathspec error."""
    if paths is not None:
        staged = set(git("diff", "--cached", "--name-only", "--relative", "-z").split("\0"))
        paths = [p for p in paths if p in staged]
        if not paths:
            raise RuntimeError("commit: nothing of this cycle is staged")
    git("commit", "-q", "-m", f"{message}\n\nMeta-Harness: {os.path.basename(__file__)}", *(["--", *paths] if paths else []))
    return git("rev-parse", "HEAD")


def undo(sha, why, found=None):
    """Puts back what `sha` changed except logs/ and memory/. Astra's commit carries those and the kit keeps writing them,
    so `git revert` refused (11/04 09:29, "local changes would be overwritten") and the cycle went unrecorded.
    A runtime switch is never put back (RUNTIME). `found` ({path: bytes}, start_contents()) is a file as the cycle found
    it: written back after the revert, so another job's uncommitted edit there is kept, not dropped for the parent's."""
    paths = [p for p in git("diff-tree", "--no-commit-id", "--name-only", "-r", "--relative", sha).splitlines()
             if p and not p.startswith(("logs/", "memory/")) and not runtime(p)]
    try:
        for p in paths:
            if git("ls-tree", "--name-only", f"{sha}^", "--", p):
                git("checkout", f"{sha}^", "--", p)
            else:
                git("rm", "-q", "-f", "--ignore-unmatch", "--", p)
        if paths and git("diff", "--cached", "--name-only", "--", *paths):
            git("commit", "-q", "-m", f"revert(week-08): {sha[:8]} 되돌림 - {why}"[:300], "--", *paths)
    except RuntimeError:              # half an undo is worse than none: every path back as the commit left it
        git("reset", "-q", "--", *paths, check=False)
        for p in paths:
            if git("ls-tree", "--name-only", "HEAD", "--", p):
                git("checkout", "HEAD", "--", p, check=False)
            elif os.path.exists(os.path.join(ROOT, p)):
                os.remove(os.path.join(ROOT, p))
        raise
    put_back(found, paths)
    log(f"undid {sha[:8]}: {why}")


def start_contents(before):
    """{path: bytes} of each RESTORE file another job had left edited at the cycle's start (`before`, dirty_paths())."""
    out = {}
    for p in RESTORE:
        if before.get(p):
            try:
                with open(os.path.join(ROOT, p), "rb") as f:
                    out[p] = f.read()
            except OSError:
                pass
    return out


def switch_shas():
    """{switch: sha1 of its bytes, None when missing} for SWITCHES and each study's adopted/disabled marker there."""
    out = {}
    markers = [os.path.relpath(m, ROOT) for k in SWITCH_MARKERS for m in glob.glob(os.path.join(ROOT, ".market", "*", k))]
    for p in list(SWITCHES) + sorted(markers):
        if os.path.isdir(os.path.join(ROOT, p)):          # a study reads disabled with exists(): a folder stops it too
            out[p] = "folder"
            continue
        try:
            with open(os.path.join(ROOT, p), "rb") as f:
                out[p] = hashlib.sha1(f.read()).hexdigest()
        except OSError:
            out[p] = None
    return out


def put_back(found, paths):
    """Writes each of `paths` held in `found` back as the cycle found it, atomically (the seller may read it meanwhile)."""
    for p in paths:
        if p in (found or {}):
            full = os.path.join(ROOT, p)
            with open(full + ".meta-undo", "wb") as f:
                f.write(found[p])
            os.replace(full + ".meta-undo", full)


def try_undo(cid, sha, why, found=None):
    """undo(), or why it could not (the change then stays as committed). The cycle is recorded either way: an Astra
    cycle missing from meta/grades.jsonl runs again on the loop's next start."""
    try:
        undo(sha, why, found)
        return None
    except (RuntimeError, OSError) as e:      # OSError: put_back could not write the file back
        log(f"{cid}: could not undo {sha[:8]}, it stays: {e}")
        return f"undo failed: {e}"[:400]


def record(rec, before=None):
    grade_log(rec)
    with open(os.path.join(ROOT, "meta", "changes.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    paths = stage(before)
    if before is not None:
        if paths and git("diff", "--cached", "--name-only", "--", *paths) and not secrets_in_stage(paths):
            commit(f"chore(week-08): 메타 하네스 기록 {rec['cycle']} - {rec['status']}"[:200], paths)
    elif git("diff", "--cached", "--name-only") and not secrets_in_stage():
        commit(f"chore(week-08): 메타 하네스 기록 {rec['cycle']} - {rec['status']}"[:200])


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
    except (OSError, ValueError, RecursionError) as e:
        return {"_unreadable": str(e)}


SAMPLE = {"settled": 0}      # counted before the model runs, so nothing it writes into the log can widen its own step


FOUND = {"pricing": None}    # pricing.json as the cycle found it when another job had left it edited, else None


def limit_problems(code):
    """What hard_limits says about the staged change: settings out of bounds or moved too far, protected files.
    When another job had left pricing.json edited (FOUND) and the cycle did not touch it, the cycle is judged on what it
    moved from there; otherwise against HEAD, as before."""
    base = FOUND["pricing"] if "pricing.json" not in code else None
    head = pricing_at_head() if base is None else base
    new = pricing_now()
    if "_unreadable" in new:
        return [f"pricing.json is not valid JSON: {new['_unreadable']}"]
    guarded = [p for p in code if p not in DOCS_ONLY]     # prose rewritten in place may drop a line naming the module
    strong = SAMPLE["settled"] >= hard_limits.STRONG_SAMPLE
    step = hard_limits.MAX_STEP_STRONG if strong else hard_limits.MAX_STEP
    owner = [f"{p} decides which buyers are refused, an owner's decision (11/05)" for p in code
             if p.split("/")[-1] == "acp_gate.py"]
    if (head.get("seller") or {}) != (new.get("seller") or {}):
        owner.append("pricing.json seller (refuse_buyers, refuse_one_star_at, refuse_exempt) is the owner's setting (11/05)")
    return owner + hard_limits.check_policy_change(head, new, step) + \
        hard_limits.guard_problems(code, git("diff", "--cached", "--no-color", "--", *guarded) if guarded else "")


def push():
    if os.environ.get("META_PUSH") == "1":
        log(git("push", "origin", f"HEAD:{os.environ.get('META_PUSH_BRANCH', 'week-08')}", check=False) or "pushed")


SCORE_SWITCH = datetime.datetime(2026, 11, 5, 3, 45, tzinfo=datetime.timezone(datetime.timedelta(hours=9))).timestamp()
BASELINE_HOURS = (6, 12)


def since_last(card, kind="terra", now=None):
    """What changed since the previous scorecard of the same kind (Terra hourly, Astra twelve-hourly: one window each),
    and against the packs 6 and 12 hours back: the log counters (failures included) and the objective. Terra grades the
    last hour from this, then the change it made in it. A window across Patch 3.3's switch (board 11/05 03:42-03:47, shop
    ALL from cash to book) is marked: its shop ALL moved with the formula, not only with trading."""
    now = time.time() if now is None else now
    folder = os.path.join(ROOT, "meta", "evidence")
    def load(p):
        try:
            with open(os.path.join(folder, p), encoding="utf-8") as f:
                x = json.load(f)
            return x if isinstance(x, dict) else None
        except (OSError, ValueError):
            return None

    def pack_time(p):
        # Git checkout/restores change mtime, not the scorecard observation time.
        value = (load(p) or {}).get("time")
        try:
            return datetime.datetime.fromisoformat(value).timestamp()
        except (ValueError, TypeError):
            return os.path.getmtime(os.path.join(folder, p))

    every = sorted((pack_time(p), p) for p in os.listdir(folder)
                   if re.fullmatch(r"(terra|astra)-\d+(-\d+)?\.json", p)
                   and pack_time(p) < now and load(p) is not None) if os.path.isdir(folder) else []
    packs = [p for _, p in every if p.startswith(kind + "-")]
    if not packs:
        return {"first": True}

    missing_cycle = None
    try:
        with open(os.path.join(ROOT, "meta", "changes.jsonl"), encoding="utf-8") as f:
            cycles = [json.loads(line) for line in f if line.strip()]
        prior = [(datetime.datetime.fromisoformat(r["time"]).timestamp(), r["cycle"])
                 for r in cycles if r.get("cycle", "").startswith(kind + "-")]
        prior = [(t, c) for t, c in prior if t < now]
        if prior:
            t, cycle = max(prior)
            if cycle + ".json" not in packs:
                missing_cycle = cycle
                candidates = [p for pt, p in every if pt <= t]
                if candidates:
                    packs = [candidates[-1]]
    except (OSError, ValueError, KeyError, TypeError):
        pass

    prev = load(packs[-1])
    if prev is None:
        return {"first": True}
    a, b = prev.get("log_counts") or {}, card.get("log_counts") or {}
    obj = lambda c: ((c.get("board") or {}).get("objective") or {})
    rotated = b.get("lines", 0) < a.get("lines", 0)        # the kit moved its log to .log.1: counts start again
    # the part written to the old file after the last pack is in market-kit.log.1 past its line a["lines"]; without it
    # the 22:26 Terra pack saw 46 shop settles of 144 (the hour before the 21:58 move was lost)
    old = scorecard.log_counts(ROOT, "market-kit.log.1", start=a.get("lines", 0)) if rotated else {}
    delta = {k: (b[k] + old.get(k, 0) if rotated else b[k] - a.get(k, 0)) for k in b if isinstance(b[k], int) and k != "tracebacks_open"}
    invalid = [k for k, v in delta.items() if v < 0]
    # Across multiple rotations .log.1 cannot reconstruct all missing segments.
    # A negative count is unavailable evidence, never negative business activity.
    for k in invalid:
        delta[k] = None
    out = {"missing_previous_cycle": missing_cycle, "log_count_gaps": invalid, "previous": packs[-1], "log_rotated": rotated, "log_counts": {k: v for k, v in delta.items() if v or v is None},
           "players": [obj(prev).get("players"), obj(card).get("players")], "shop": [prev.get("shop"), card.get("shop")],
           "across_score_switch": pack_time(packs[-1]) < SCORE_SWITCH <= now, "baselines": {}}
    for h in BASELINE_HOURS:      # the newest pack at least h hours old, any kind: the 6 and 12 hour trend
        old = [p for t, p in every if t <= now - h * 3600]
        base = load(old[-1]) if old else None
        if base is None:
            out["baselines"][f"{h}h"] = None
            continue
        t = pack_time(old[-1])
        out["baselines"][f"{h}h"] = {"pack": old[-1], "hours": round((now - t) / 3600, 1),
                                     "players": [obj(base).get("players"), obj(card).get("players")],
                                     "shop_all": [(obj(base).get("shops") or {}).get("all"), (obj(card).get("shops") or {}).get("all")],
                                     "buyer_all": [(obj(base).get("buyers") or {}).get("all"), (obj(card).get("buyers") or {}).get("all")],
                                     "across_score_switch": t < SCORE_SWITCH <= now}
    return out


def alert_problems(card, answer, code):
    """A cycle that changes nothing while the scorecard raises an alert (llm cap, surge above the worst cost, forum falling,
    drift, rules) must answer each one as "alert <name>: ..." in grade_summary or evidence_gap: no_change cannot pass one
    over, and a plain word like "forum" in a summary does not count as an answer."""
    if code or not answer:
        return []
    said = json.dumps({k: answer.get(k) for k in ("grade_summary", "evidence_gap")}, ensure_ascii=False)
    return [f"alert left open with no change: {a}" for a in card.get("alerts") or []
            if f"alert {a.split(':')[0]}:" not in said]


def grade_log(rec):
    """meta/grades.jsonl: each cycle's grading, apart from the changes, so a trend of grades can be read in one place."""
    a = rec.get("answer") or {}
    with open(os.path.join(ROOT, "meta", "grades.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"cycle": rec["cycle"], "time": rec["time"], "status": rec.get("status"),
                            "grade": a.get("grade_summary") or a.get("summary"), "weakest": a.get("weakest"),
                            "action": a.get("action_kind"), "verdict": a.get("previous_verdict"), "grades": a.get("grades"),
                            "keep_or_drop": a.get("keep_or_drop"), "strategy_check": a.get("strategy_check"),
                            "strategy": a.get("strategy"), "evidence_gap": a.get("evidence_gap")}, ensure_ascii=False) + "\n")


GAP_ID = re.compile(r"(?<![0-9A-Za-z-])(?:u-[0-9a-f]{8,}|G[0-9a-f]{8})(?![0-9A-Za-z])")   # unit u-d9db11f14a2a, SKU G8ec51935
GAP_REPEAT = 2                         # cycles an id stays named as missing before this cycle must measure it


def gap_ids(text):
    return set(GAP_ID.findall(text or ""))


def open_gaps(n=GAP_REPEAT):
    """{id: [first cycle, ...]} for each unit or SKU named as missing in each of the last n recorded cycles (any status,
    any kind): the evidence gap that is carried instead of measured."""
    try:
        with open(os.path.join(ROOT, "meta", "grades.jsonl"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    last = []
    for line in reversed(lines):
        try:
            g = json.loads(line)
        except ValueError:
            continue
        if isinstance(g, dict) and isinstance(g.get("cycle"), str):
            last.append(g)
            if len(last) == n:
                break
    if len(last) < n:
        return {}
    named = [gap_ids(((g.get("evidence_gap") or {}) if isinstance(g.get("evidence_gap"), dict) else {}).get("missing"))
             for g in last[::-1]]
    return {i: [g["cycle"] for g in last[::-1]] for i in sorted(set.intersection(*named))}


def gap_problems(answer, code, carried=None):
    """A cycle that changes nothing because evidence is missing must have planned how to get it: the measurement and the
    line that will judge it next cycle. Overnight 11/03-04 Terra ended 8 cycles on "hard to tell" with neither.
    A unit or SKU still missing after GAP_REPEAT cycles (`carried`, open_gaps()) must be measured in this one: named in
    the measurement and quoted from a line that holds it. "G8ec51935 ... 원가 누락 1건" was carried cycle after cycle while
    measure_r3 held the unit's cost (GPT Pro 11/09 07:38 9-3). A quote from meta/ (the harness's own records, which repeat
    the earlier gap sentence) does not count, and a carried id dropped from missing needs such a quote too: the line
    that resolved it."""
    if not answer or code:
        return []
    gap = answer.get("evidence_gap") or {}
    line = gap.get("judge_line")
    whole = isinstance(line, dict) and line.get("direction") in ("above", "below") and \
        all(isinstance(line.get(k), str) and line[k].strip() for k in JUDGE_FIELDS)
    if (gap.get("missing") or "").strip() and not ((gap.get("measurement") or "").strip() and whole):
        return ["evidence is missing but no measurement or whole judge line (metric, direction, threshold, min_sample, "
                "expires, undo) was set (meta/terra.md 'When evidence is short')"]
    out = []
    quoted = " ".join(str(e.get("excerpt") or "") for e in answer.get("evidence") or []
                      if isinstance(e, dict) and outside_meta(e.get("ref")))
    still = gap_ids(gap.get("missing"))
    for i in sorted(carried or {}):
        if i in still and (i not in (gap.get("measurement") or "") or i not in quoted):
            out.append(f"evidence gap {i} open since {carried[i][0]}: name it in the measurement and quote this cycle's "
                       f"line of it outside meta/ (unit, cost, supply window or settlement)")
        elif i not in still and i not in quoted:
            out.append(f"evidence gap {i} open since {carried[i][0]} is no longer listed as missing: quote the line "
                       f"outside meta/ that resolved it")
    return out


def read_queue(text=None):
    """The queue's entries (dicts with a string id), from meta/experiments.json or the given text; anything else is []."""
    try:
        if text is None:
            with open(os.path.join(ROOT, EXPERIMENTS), encoding="utf-8") as f:
                text = f.read()
        queue = json.loads(text).get("queue")
    except (OSError, ValueError, AttributeError):
        return []
    return [e for e in queue if isinstance(e, dict) and isinstance(e.get("id"), str)] if isinstance(queue, list) else []


def hours_of(e):
    h = e.get("hours")
    return h if isinstance(h, (int, float)) and not isinstance(h, bool) and 0 < h <= 168 else 12


def experiments(now=None):
    """The queue as the pack shows it: the one running (when it started, its expiry, whether that passed), the next one
    queued and how many wait. A queue entry: id, change, evidence, metric, direction, threshold, min_sample, hours, undo.
    What runs comes from meta/experiments_log.jsonl alone (its start line keeps the entry as it was), so an entry edited
    or removed from the queue while it runs is still the one judged; an id the log already holds never starts again."""
    now = time.time() if now is None else now
    queue = read_queue()
    steps, began = {}, {}
    for r in scorecard.jsonl(os.path.join(ROOT, EXPERIMENT_LOG)):
        if isinstance(r.get("id"), str) and isinstance(r.get("ts"), (int, float)):
            steps[r["id"]] = r.get("step")
            if r.get("step") == "start":
                began[r["id"]] = r
    run = next((i for i, st in steps.items() if st in ("start", "continue") and i in began), None)
    waiting = [e for e in queue if e["id"] not in steps]
    out = {"running": None, "next": waiting[0] if waiting else None, "waiting": len(waiting),
           "closed": {i: st for i, st in steps.items() if st in ("done", "drop")},
           "skipped": [e["id"] for e in queue if steps.get(e["id"]) in ("done", "drop")]}
    if run:
        b = began[run]
        e = b.get("entry") if isinstance(b.get("entry"), dict) else next((q for q in queue if q["id"] == run), {"id": run})
        hours, ends = hours_of(e), b["ts"] + hours_of(e) * 3600
        out["running"] = {**e, "id": run, "hours": hours, "started": time.strftime("%m-%d %H:%M", time.localtime(b["ts"])),
                          "expires": time.strftime("%m-%d %H:%M", time.localtime(ends)), "expired": now > ends}
    return out


def experiment_problems(kind, answer, view, code=()):
    """One evidenced experiment at a time, from the queue in order (meta/terra.md 'Experiments'): start only the next
    one, only when none runs and only with its change in this cycle's code; judge the running one once its hours pass;
    with none running either start the next or say why not in result. A cycle that breaks this is not accepted."""
    if kind != "terra" or not answer:
        return []
    x = answer.get("experiment") or {}
    step, eid, said = x.get("step") or "none", x.get("id") or "", (x.get("result") or "").strip()
    run, nxt = view.get("running"), view.get("next")
    if step == "start":
        if run:
            return [f"experiment {eid}: {run['id']} still runs; judge it (done or drop) before another starts"]
        if not nxt or eid != nxt["id"]:
            return [f"experiment {eid}: only the next one in the queue ({nxt['id'] if nxt else 'none'}) can start"]
        if not [c for c in code if c not in DOCS_ONLY]:
            return [f"experiment {eid}: a start makes its change in this cycle (no code or settings changed)"]
        return []
    if step in ("continue", "done", "drop"):
        if not run or eid != run["id"]:
            return [f"experiment {eid}: not the running one ({run['id'] if run else 'none'})"]
        if step == "continue" and run.get("expired"):
            return [f"experiment {eid} passed its {run['hours']} h at {run['expires']}: judge it (done or drop)"]
        if step != "continue" and not said:
            return [f"experiment {eid} {step}: result must give the metric's number against its threshold and the sample"]
        return []
    if run and run.get("expired"):
        return [f"experiment {run['id']} passed its {run['hours']} h at {run['expires']}: judge it (done or drop)"]
    if not run and nxt and not said:
        return [f"experiment {nxt['id']} is next in the queue: start it or say in result why not now (the number that blocks it)"]
    return []


def queue_problems(kind, paths):
    """Terra only adds at the end of the queue (Astra orders it), and the log is the harness's: a model's write to it is
    put back and refused."""
    out = []
    if EXPERIMENT_LOG in paths:
        out.append(f"{EXPERIMENT_LOG} is written by the harness only")
        if git("ls-tree", "HEAD", "--", EXPERIMENT_LOG, check=False):        # in HEAD: back to it
            git("checkout", "HEAD", "--", EXPERIMENT_LOG, check=False)
        else:
            git("rm", "-q", "--cached", "--", EXPERIMENT_LOG, check=False)
            if os.path.exists(os.path.join(ROOT, EXPERIMENT_LOG)):
                os.remove(os.path.join(ROOT, EXPERIMENT_LOG))
    if kind == "terra" and EXPERIMENTS in paths:
        try:
            old = read_queue(git("show", f"HEAD:./{EXPERIMENTS}"))
        except RuntimeError:
            old = []
        if read_queue()[:len(old)] != old:
            out.append(f"Terra changed or removed entries of {EXPERIMENTS}; it may only add at the end")
    return out


def note_experiment(cid, answer):
    """After an accepted cycle: its experiment step goes into the log the next pack reads; a start keeps its entry."""
    x = (answer or {}).get("experiment") or {}
    if x.get("step") in ("start", "continue", "done", "drop") and x.get("id"):
        rec = {"ts": time.time(), "cycle": cid, "id": x["id"], "step": x["step"], "result": x.get("result") or ""}
        if x["step"] == "start":
            rec["entry"] = next((e for e in read_queue() if e["id"] == x["id"]), None)
        with open(os.path.join(ROOT, EXPERIMENT_LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def last_cycle_time(kind):
    """When the last `kind` cycle began, from meta/grades.jsonl (any status), or None. The loop keeps its rhythm across
    restarts with it: Astra had never run because each restart of the loop restarted its twelve hours."""
    try:
        with open(os.path.join(ROOT, "meta", "grades.jsonl"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in (reversed(lines) if kind != "first" else lines):
        try:
            g = json.loads(line)
            if kind == "first" or str(g.get("cycle", "")).startswith(f"{kind}-"):
                return datetime.datetime.fromisoformat(g["time"]).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
    return None


def due(kind, now=None):
    """Seconds until the next `kind` cycle (0 or less: now). Terra an hour of trading after its last (recorded once the
    model answered, before the ten-minute watch); Astra twelve hours after its last, or after the first cycle of the
    round (or this loop's start) when it has not run yet."""
    now = time.time() if now is None else now
    if kind == "terra":
        last = last_cycle_time("terra")
        return (last + CANARY_SECONDS + RUN_SECONDS - now) if last else 0
    last = last_cycle_time("astra") or last_cycle_time("first") or STARTED
    return last + ASTRA_EVERY - now


ACCEPTED = ("no_change", "running")     # cycles whose answer the harness took: a refused or failed one is no evidence
SHIFT_GAP = 3                          # Terra cycles a shift stands before the next one


def recent_strategy_checks(n=SHIFT_GAP):
    """The strategy checks of Terra's last n accepted cycles in meta/grades.jsonl, oldest first."""
    try:
        with open(os.path.join(ROOT, "meta", "grades.jsonl"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            g = json.loads(line)
        except ValueError:
            continue
        if str(g.get("cycle", "")).startswith("terra-") and g.get("status") in ACCEPTED and isinstance(g.get("strategy_check"), dict):
            out.append(g["strategy_check"])
            if len(out) == n:
                break
    return out[::-1]


def strategy_problems(kind, answer, paths):
    """Terra shifts the strategy only when its previous accepted cycle said watch, no shift stood in its last three cycles,
    and the new strategy is written in meta/strategy.md; it touches that file only to shift. So one noisy hour cannot turn
    the shop around, and a shift is not undone by the next."""
    if kind != "terra" or not answer:
        return []
    check = answer.get("strategy_check") or {}
    shifting = check.get("keep_or_shift") == "shift" or answer.get("action_kind") == "strategy_shift"
    if not shifting:
        return [f"Terra rewrites {STRATEGY} only with a strategy shift"] if STRATEGY in paths else []
    out = []
    if check.get("keep_or_shift") != "shift":
        out.append("action_kind strategy_shift needs strategy_check.keep_or_shift = shift")
    recent = recent_strategy_checks()
    if not recent or recent[-1].get("keep_or_shift") != "watch":
        out.append("a strategy shift needs the previous accepted Terra cycle to have said watch (two cycles of evidence)")
    if any(c.get("keep_or_shift") == "shift" for c in recent):
        out.append(f"a strategy shift stands {SHIFT_GAP} Terra cycles before the next")
    if STRATEGY not in paths:
        out.append(f"a strategy shift must rewrite {STRATEGY}")
    return out


def refresh_scorecard():
    """Pick up scorecard changes made while the long-running loop was alive."""
    importlib.invalidate_caches()
    return importlib.reload(scorecard)


def cycle(kind):
    before = dirty_paths()        # what the folder already held: not this cycle's change
    FOUND["pricing"] = pricing_now() if "pricing.json" in before else None
    found = start_contents(before)
    restart_pricing_before, restart_pricing_head = restart_pricing_snapshot(before, found)
    cid = f"{kind}-{time.strftime('%m%d-%H%M%S')}"
    os.makedirs(os.path.join(ROOT, "meta", "evidence"), exist_ok=True)
    pack = os.path.join(ROOT, "meta", "evidence", f"{cid}.json")
    # The loop can survive code updates for many hours. Load the current score
    # formula before each pack rather than retaining the version from startup.
    card = refresh_scorecard().build(ROOT, record=True)
    card["since_last_cycle"] = since_last(card, kind)
    card["experiments"] = view = experiments()
    card["open_gaps"] = carried = open_gaps()
    lc = card.get("log_counts") or {}
    SAMPLE["settled"] = lc.get("settled_as_shop", 0) + lc.get("paid_as_buyer", 0)
    with open(pack, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=1, default=str)
    log(f"{cid}: asking {MODELS[kind]}")
    switches = switch_shas()
    answer, err = call_model(kind, cid, pack)
    rec = {"cycle": cid, "model": MODELS[kind], "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "answer": answer}
    after = switch_shas()
    flipped = sorted(p for p in set(switches) | set(after) if switches.get(p) != after.get(p))
    if flipped:
        rec["switches_changed"] = flipped
        log(f"{cid}: study switches changed while the model ran, left as they are: {flipped}")
    paths = stage(before)
    code = [p for p in paths if not p.startswith(NOT_CODE) and not p.startswith(("logs/", "memory/"))]

    if paths and secrets_in_stage(paths):      # nothing goes into history, the attempt included
        git("reset", "-q", "--", *paths)
        subprocess.run(["git", "checkout", "--", *code], cwd=ROOT, check=False)
        for p in code:
            if os.path.exists(os.path.join(ROOT, p)) and git("ls-files", "--", p, check=False) == "":
                os.remove(os.path.join(ROOT, p))
        put_back(found, code)
        rec.update(status="discarded_secret", reason="a secret was about to be committed")
        log(f"{cid}: DISCARDED, a secret was in the change")
        record(rec, before)
        return rec

    problems = [err] if err else []
    problems += [f"{p} changed while the model ran: a study switch is not the model's to set (left as it is)"
                 for p in flipped]
    if answer is not None:
        moved_refs = anchor_evidence(answer.get("evidence"))
        if moved_refs:
            rec["evidence_reanchored"] = moved_refs
            log(f"{cid}: citations moved to the line that holds them: {'; '.join(moved_refs)[:400]}")
        problems += verify_evidence(answer.get("evidence"))
        problems += strategy_problems(kind, answer, code)
        problems += gap_problems(answer, code, carried)
        problems += alert_problems(card, answer, code)
        problems += experiment_problems(kind, answer, view, code)
    problems += limit_problems(code)
    problems += queue_problems(kind, paths)
    moved = hard_limits.policy_diff(pricing_at_head(), pricing_now()) if "pricing.json" in paths else []
    if moved:
        log(f"{cid}: settings moved: {'; '.join(moved)}")
    docs_only = bool(code) and all(p in DOCS_ONLY for p in code)
    if code and not problems and not docs_only:
        ok, output = run_tests()
        if not ok:
            problems.append(f"tests failed: {output[:2500]}")       # the FAIL/ERROR names first, then the tail
    if err and not code:
        rec.update(status="model_error", reason=err)
        log(f"{cid}: {err}")
        record(rec, before)
        return rec
    if not code:
        rec.update(status="no_change" if not problems else "no_change_unsupported", problems=problems)
        if not problems:
            note_experiment(cid, answer)
        log(f"{cid}: no change ({len(problems)} citation problems)")
        record(rec, before)
        return rec

    title = (answer or {}).get("change_summary") or err or "변경"
    if problems:
        sha = commit(f"wip(week-08): [{cid}] 실패 - {title} / {problems[0]}"[:300], paths)
        failed = try_undo(cid, sha, f"{cid} 검증 실패: {problems[0]}", found)
        rec.update(status="rejected_kept" if failed else "rejected", problems=problems + ([failed] if failed else []),
                   commit=sha)
        record(rec, before)
        return rec

    why = (answer or {}).get("hypothesis") or ""
    body = "\n".join(x for x in (f"이유: {why}" if why else "", ("설정: " + "; ".join(moved)) if moved else "",
                                 f"기대: {(answer or {}).get('target_metric', '')} {(answer or {}).get('expected', '')}".strip()) if x)
    sha = commit(f"feat(week-08): [{cid}] {title}"[:300] + (f"\n\n{body}" if body else ""), paths)
    if restart_pricing_head != git("rev-parse", f"{sha}^", check=False):
        restart_pricing_before = None   # bind the actual commit parent, including concurrent commits during commit()
    rec.update(commit=sha, files=code, settings=moved)
    log(f"{cid}: applied {sha[:8]}: {title}")
    if docs_only:
        note_experiment(cid, answer)
        rec.update(status="running")
        log(f"{cid}: strategy document only, nothing to watch")
        record(rec, before)
        return rec
    restart_seller(code, pricing_before=restart_pricing_before)
    bad = canary()
    if bad:
        log(f"{cid}: failed the watch: {'; '.join(bad)}")
        restart_pricing_before_undo = pricing_now()   # rollback compares the applied state, not the cycle's original state
        failed = try_undo(cid, sha, f"{cid} 10분 점검 실패: {'; '.join(bad)}", found)
        if failed:
            rec.update(status="canary_failed_kept", problems=bad + [failed])
        else:
            restart_seller(code, pricing_before=restart_pricing_before_undo)
            rec.update(status="canary_failed", problems=bad)
    else:
        note_experiment(cid, answer)    # only a change that stays: one the watch undid took its experiment step with it
        rec.update(status="running")
        log(f"{cid}: passed the watch")
    record(rec, before)
    return rec


def market_round():
    try:
        return httpx.get(f"{scorecard.MARKET}/api/stats", timeout=15).json()["round"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def loaded_sources():
    """The files of this folder's modules the loop has imported (meta_harness, hard_limits, scorecard and theirs)."""
    root = os.path.realpath(ROOT) + os.sep
    out = set()
    for m in list(sys.modules.values()):
        p = getattr(m, "__file__", None)
        if isinstance(p, str) and p.endswith(".py") and os.path.realpath(p).startswith(root):
            out.add(os.path.realpath(p))
    return sorted(out)


def code_stamp(paths):
    """{path: sha1 of its source, or None when it cannot be read}."""
    out = {}
    for p in paths:
        try:
            with open(p, "rb") as f:
                out[p] = hashlib.sha1(f.read()).hexdigest()
        except OSError:
            out[p] = None
    return out


def stale_code(stamp):
    """The names of the files whose source is no longer what the loop loaded. The loop lives for days and only scorecard
    is reloaded: 2327f1c fixed verify_evidence at 11/08 23:44, the loop started at 14:08 kept the old function, and
    terra-1109-002601 and astra-1109-011816 were reverted for quoting a whole "OK" line (GPT Pro 11/09 01:39 6.3)."""
    now = code_stamp(stamp)
    return sorted(os.path.basename(p) for p in stamp if now[p] != stamp[p])


def load_tried():
    try:
        with open(TRIED, encoding="utf-8") as f:
            t = json.load(f)
        if not isinstance(t, dict):
            return {}
        out = {}
        for k, v in t.items():          # never a time that stalls a kind: not a number, not finite, in the future
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                v = float(v)
                if math.isfinite(v) and v <= time.time() + 60:
                    out[k] = v
        return out
    except (OSError, ValueError, AttributeError, OverflowError):
        return {}


def save_tried(tried):
    try:
        os.makedirs(os.path.dirname(TRIED), exist_ok=True)
        tmp = TRIED + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tried, f)
        os.replace(tmp, TRIED)
    except OSError as e:
        log(f"could not keep the cycle times: {e}")


_BROKEN = set()


def restart_if_stale(stamp):
    """Leave between cycles when the code changed: run_meta.sh starts the loop again in 60 s with the new code."""
    old = stale_code(stamp)
    if not old:
        return
    for p in stamp:                     # missing, half-written or broken: keep the running code rather than crash every 60 s
        try:
            with open(p, "rb") as f:
                compile(f.read(), p, "exec")
        except (SyntaxError, ValueError, OSError) as e:
            why = f"code changed ({', '.join(old)}) but {os.path.basename(p)} does not compile, staying on the old code: {e}"
            if why not in _BROKEN:      # once per broken state, not every minute
                _BROKEN.add(why)
                log(why)
            return
    log(f"code changed since the loop started ({', '.join(old)}): restarting to load it")
    sys.exit(0)


def run():
    """Terra, then its hour of trading; Astra when twelve hours have passed. Nothing happens before Round 3: no data."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    stamp = code_stamp(loaded_sources())
    tried = load_tried()              # a cycle that crashed before recording still waits its turn, across a restart too
    while True:
        if (market_round() or 0) < ECON_ROUND:
            log("Round 3 has not opened, waiting")
            time.sleep(300)
            continue
        every = {"terra": RUN_SECONDS, "astra": ASTRA_EVERY}
        wait = lambda k: max(due(k), tried.get(k, 0) + every[k] - time.time())
        for kind in ("terra", "astra"):
            if wait(kind) > 0:
                continue
            restart_if_stale(stamp)  # a preceding cycle may have changed our loaded dependencies
            tried[kind] = time.time()
            save_tried(tried)
            try:
                cycle(kind)
            except Exception as e:   # noqa: BLE001 - the shop runs on regardless; the next cycle tries again
                log(f"{kind} cycle failed: {type(e).__name__}: {e}")
            push()
        restart_if_stale(stamp)
        nxt = {k: time.strftime("%m-%d %H:%M", time.localtime(time.time() + max(0, wait(k)))) for k in every}
        log(f"next terra {nxt['terra']}, astra {nxt['astra']}")
        end = time.time() + min(RUN_SECONDS, max(60, min(wait("terra"), wait("astra"))))
        while time.time() < end:          # a push during the wait is picked up within a minute, not after the hour
            time.sleep(max(0, min(60, end - time.time())))
            restart_if_stale(stamp)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("terra", "astra", "run"):
        sys.exit(__doc__)
    if sys.argv[1] == "run":
        run()
    else:
        print(json.dumps(cycle(sys.argv[1]), ensure_ascii=False, indent=1))
