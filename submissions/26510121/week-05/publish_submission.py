"""Publish the checked submission using existing GitHub credentials only."""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
from verify_evidence import ROOT, inspect

REPO = ROOT.parents[2]


def run(args, env, **kwargs):
    result = subprocess.run(args, cwd=REPO, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", **kwargs)
    if result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def github_environment():
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    # Credential-helper output is captured in memory; never print or store it.
    value = subprocess.run(["git", "credential", "fill"], cwd=REPO, env=env,
                           input="protocol=https\nhost=github.com\n\n", capture_output=True,
                           text=True, timeout=30)
    fields = dict(line.split("=", 1) for line in value.stdout.splitlines() if "=" in line)
    if value.returncode or not fields.get("password"):
        raise RuntimeError("Existing GitHub credentials unavailable; authenticate locally with gh auth login")
    env["GH_TOKEN"] = fields["password"]
    login = run(["gh", "api", "user", "--jq", ".login"], env, timeout=30)
    if login != "seochanit":
        raise RuntimeError("GitHub credential belongs to a different account; log in as seochanit")
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-auth", action="store_true")
    args = parser.parse_args()
    env = github_environment()
    if args.check_auth:
        print("PASS: existing Git credential authenticates GitHub as seochanit; no credentials saved")
        return
    rows, _, _ = inspect()
    assert "all checks passed" in (ROOT / "checks/final-structure.txt").read_text(encoding="utf-8")
    assert not run(["git", "status", "--porcelain"], env), "Commit all work before publishing"
    branch = run(["git", "branch", "--show-current"], env)
    assert branch == "week-05", "Unexpected submission branch"
    base = run(["gh", "api", "repos/Q00/ai-agent-engineering-101", "--jq", ".default_branch"], env, timeout=30)
    # No force push and no history rewriting.
    print(run(["git", "push", "--set-upstream", "origin", branch], env, timeout=120))
    existing = json.loads(run(["gh", "pr", "list", "--repo", "Q00/ai-agent-engineering-101",
                              "--head", "seochanit:week-05", "--state", "open", "--json", "url"], env, timeout=30))
    if existing:
        print("Existing open PR: " + existing[0]["url"])
        return
    body = ("Implements the Week 05 authenticated Streamable HTTP negotiation market and isolated Codex CLI host. "
            "The runner records actual tool calls, responses and per-episode metrics for both required injection conditions.\n\n"
            f"Validation: 38 live HTTP tests; {len(rows)} real gpt-6-luna episodes (4 scenarios × 3 repeats × 2 conditions); "
            "four authorization checks; independent CSV/CLI/server evidence reconciliation; official week05 structural checker passed.\n\n"
            "Failed pilot settings, corrections and raw logs are retained in separate commits. "
            "Report, settings and reproduction commands: submissions/26510121/week-05/REPORT.md.\n")
    file = ROOT / ".runtime/pr-body.md"
    file.write_text(body, encoding="utf-8")
    url = run(["gh", "pr", "create", "--repo", "Q00/ai-agent-engineering-101", "--base", base,
               "--head", "seochanit:week-05", "--title", "[week-05] 26510121", "--body-file", str(file)], env, timeout=60)
    print(url)


if __name__ == "__main__":
    main()
