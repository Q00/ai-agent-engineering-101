"""CI for week-08: the checks a commit must pass before ops/deploy_pull.py deploys it. Local agents run the same file
before pushing:  python submissions/26510358/week-08/ops/ci.py            (HEAD against hub/week-08)
                 python submissions/26510358/week-08/ops/ci.py --rev <sha> --base <sha>

The commit is checked out into a sparse throwaway worktree (scripts/ + week-08 without logs/), so the working copy
and the real keys are never touched. Checks, in order (the first failure does not stop the rest):
  scope     every changed path is inside submissions/26510358/ (AGENTS.md invariant 1)
  secrets   no key/env file, no private key or API token in the added lines (invariant 3)
  compile   py_compile of every .py in week-08
  lint      ruff E9,F63,F7,F82 (syntax errors, undefined names, misplaced break/return) on the changed .py files
  imports   the modules the service loads import cleanly against the official kit (scripts/market_kit.py)
  pytest    the whole suite (throwaway Ed25519 keys stand in for shop_key.pem / buyer_key.pem)
  config    changed r3lib/config.py constants, listed for the reviewer (never fails)
Prints a JSON report; exit 0 only when every check passed.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WEEK08 = Path(__file__).resolve().parents[1]
SCOPE = "submissions/26510358/"
REL = SCOPE + "week-08/"
VENV = Path(os.environ.get("CI_VENV", str(Path.home() / ".venvs/week08-qa/bin")))
SECRET_PATH = re.compile(r"(\.pem|\.env|api-key[^/]*|owner\.env|\.key)$", re.I)
SECRET_TEXT = re.compile(r"BEGIN [A-Z ]*PRIVATE KEY|sk-[A-Za-z0-9_-]{20,}|xai-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}"
                         r"|sbp_[A-Za-z0-9]{20,}|eyJhbGciOi[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")
SERVICE_MODULES = ("kit_patches", "market_runtime", "shop_r3", "buyer_r3", "restock_r3", "forum_r3", "review_r3",
                   "r3lib.org.server")


def sh(cmd, cwd=None, timeout=1200):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def git(repo, *args):
    code, out = sh(["git", "-C", str(repo), *args])
    if code:
        raise RuntimeError(f"git {' '.join(args[:3])}: {out[:300]}")
    return out


def tail(text, n=1500):
    keep = [x for x in text.splitlines() if x.strip()]
    return "\n".join(keep)[-n:]


def check_scope(paths):
    bad = [p for p in paths if not p.startswith(SCOPE)]
    return not bad, "outside " + SCOPE + ": " + ", ".join(bad[:10]) if bad else f"{len(paths)} paths in scope"


def check_secrets(repo, base, sha, paths):
    bad = [p for p in paths if SECRET_PATH.search(p)]
    diff = git(repo, "diff", "--no-color", "-U0", base, sha, "--", *paths) if paths else ""
    hits = [ln[:60] + "..." for ln in diff.splitlines() if ln.startswith("+") and SECRET_TEXT.search(ln)]
    ok = not bad and not hits
    return ok, "clean" if ok else f"files {bad[:5]} lines {hits[:5]}"


def check_compile(w8):
    files = [str(p) for p in w8.rglob("*.py") if "logs" not in p.relative_to(w8).parts]
    code, out = sh([str(VENV / "python"), "-m", "py_compile", *files])
    return code == 0, tail(out) or f"{len(files)} files"


def check_lint(w8, paths):
    py = [str(Path(w8.parents[2], p)) for p in paths if p.startswith(REL) and p.endswith(".py")
          and Path(w8.parents[2], p).exists()]
    if not py:
        return True, "no changed .py"
    code, out = sh([str(VENV / "ruff"), "check", "--select", "E9,F63,F7,F82", "--quiet", *py])
    return code == 0, tail(out) or f"{len(py)} files clean"


def check_imports(w8, wt):
    prog = (f"import sys; sys.path.insert(0, {str(wt / 'scripts')!r})\n"
            + "".join(f"import {m}\n" for m in SERVICE_MODULES) + "print('ok')")
    code, out = sh([str(VENV / "python"), "-c", prog], cwd=w8, timeout=60)
    return code == 0, tail(out, 800)


def check_pytest(w8):
    code, out = sh([str(VENV / "pytest"), "-q", "-p", "no:cacheprovider", "tests"], cwd=w8)
    keep = [x for x in out.splitlines() if "FAILED" in x or "ERROR" in x or re.search(r"\d+ (passed|failed)", x)]
    return code == 0, "\n".join(keep)[-1500:]


def config_changes(repo, base, sha):
    f = REL + "r3lib/config.py"
    try:
        diff = git(repo, "diff", "--no-color", "-U0", base, sha, "--", f)
    except RuntimeError:
        return []
    out = []
    for ln in diff.splitlines():
        m = re.match(r"^([+-])([A-Z][A-Z0-9_]+)\s*=\s*([^#]+)", ln)
        if m:
            out.append(f"{m.group(1)}{m.group(2)} = {m.group(3).strip()}")
    return out


def run(repo, base, sha):
    """All checks on sha (diff against base). Returns the report dict."""
    t0 = time.time()
    paths = git(repo, "diff", "--name-only", base, sha).splitlines()
    report = {"sha": sha[:8], "base": base[:8], "checks": []}

    def add(name, result):
        ok, detail = result
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    add("scope", check_scope(paths))
    add("secrets", check_secrets(repo, base, sha, paths))
    tmp = Path(tempfile.mkdtemp(prefix="ci-wt-"))
    wt = tmp / "wt"
    try:
        git(repo, "worktree", "add", "--no-checkout", "--detach", str(wt), sha)
        git(wt, "sparse-checkout", "init", "--no-cone")
        Path(wt, git(wt, "rev-parse", "--git-path", "info/sparse-checkout")).write_text(
            f"/scripts/\n/{REL}\n!/{REL}logs/\n")
        git(wt, "checkout", "-q", "--detach", sha)
        w8 = wt / REL
        for k in ("shop_key.pem", "buyer_key.pem"):
            sh(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(w8 / k)])
        add("compile", check_compile(w8))
        add("lint", check_lint(w8, paths))
        add("imports", check_imports(w8, wt))
        add("pytest", check_pytest(w8))
    except Exception as e:
        add("checkout", (False, f"{type(e).__name__}: {str(e)[:300]}"))
    finally:
        sh(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)])
        shutil.rmtree(tmp, ignore_errors=True)
        sh(["git", "-C", str(repo), "worktree", "prune"])
    report["config_changes"] = config_changes(repo, base, sha)
    report["ok"] = all(c["ok"] for c in report["checks"])
    report["seconds"] = round(time.time() - t0, 1)
    return report


def summary(report):
    """One short text block, used as the git note agents read back from the hub."""
    lines = [f"CI {'PASS' if report['ok'] else 'FAIL'} {report['sha']} (base {report['base']}, {report['seconds']} s)"]
    for c in report["checks"]:
        first = (c["detail"] or "").splitlines()[-1:] if c["ok"] else (c["detail"] or "").splitlines()[-6:]
        lines.append(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['name']}: " + " | ".join(first))
    if report.get("config_changes"):
        lines.append("  config: " + "; ".join(report["config_changes"][:12]))
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--base", default=None, help="default: merge-base with hub/week-08 (or origin/week-08)")
    a = ap.parse_args()
    repo = Path(git(WEEK08, "rev-parse", "--show-toplevel"))
    sha = git(repo, "rev-parse", a.rev)
    base = a.base
    if not base:
        for ref in ("hub/week-08", "origin/week-08"):
            code, out = sh(["git", "-C", str(repo), "merge-base", sha, ref])
            if code == 0:
                base = out
                break
    base = git(repo, "rev-parse", base or f"{sha}~1")
    rep = run(repo, base, sha)
    print(summary(rep))
    print(json.dumps(rep, ensure_ascii=False))
    sys.exit(0 if rep["ok"] else 1)
