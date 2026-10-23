"""Inspect/create the course PR using existing Git credentials; never print credentials."""

import argparse
import json
import os
from pathlib import Path
import ssl
import subprocess

import httpx

ROOT = Path(__file__).resolve().parent
REPO = "Q00/ai-agent-engineering-101"
HEAD = "kcwarni:week-07"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
        result = subprocess.run(
            ["git", "-c", "credential.interactive=false", "credential", "fill"],
            input="protocol=https\nhost=github.com\nusername=kcwarni\n\n", capture_output=True,
            text=True, env=env, cwd=ROOT)
        if result.returncode:
            raise SystemExit("No existing GitHub credential available; sign in through Git Credential Manager.")
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        token = fields.get("password")
    if not token:
        raise SystemExit("No existing GitHub credential available.")
    with httpx.Client(verify=ssl.create_default_context(), timeout=45,
                      headers={"Authorization": f"Bearer {token}",
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"}) as client:
        def api(method, route, **kwargs):
            response = client.request(method, "https://api.github.com" + route, **kwargs)
            if response.is_error:
                # Status only; avoid leaking server errors containing sensitive request details.
                raise SystemExit(f"GitHub API returned HTTP {response.status_code} for {method} {route}")
            return response.json()

        account = api("GET", "/user")["login"]
        if account.lower() != "kcwarni":
            raise SystemExit("Existing credential belongs to a different account; submission stopped.")
        pulls = api("GET", f"/repos/{REPO}/pulls", params={"state": "all", "head": HEAD, "per_page": 100})
        print(json.dumps({"account": account, "existing_prs": [
            {k: p[k] for k in ("number", "title", "state", "html_url", "created_at")}
            for p in pulls]}, ensure_ascii=False))
        if args.create:
            existing = next((p for p in pulls if p["state"] == "open"), None)
            if existing:
                print(json.dumps({"pr_url": existing["html_url"], "created": False}))
                return
            body = (ROOT / "PR-BODY.md").read_text(encoding="utf-8")
            pr = api("POST", f"/repos/{REPO}/pulls", json={
                "title": "[week-07] 24522014", "head": HEAD, "base": "main", "body": body})
            print(json.dumps({"pr_url": pr["html_url"], "created": True,
                              "created_at": pr["created_at"]}))


if __name__ == "__main__":
    main()
