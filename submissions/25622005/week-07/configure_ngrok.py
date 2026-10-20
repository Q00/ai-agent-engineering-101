"""One-time local setup; the authtoken is masked and stored only in .market/.

Run from the repository root:
    .venv/bin/python submissions/25622005/week-07/configure_ngrok.py

Get the assigned public domain and agent authtoken from your own ngrok
dashboard. Nothing is submitted to the market by this setup command.
"""
import getpass
import json
import os
from pathlib import Path
import subprocess

from fixed_address_runtime import load_fixed_config


def prepare_config(folder, url):
    """Build public tunnel arguments without including credentials."""
    private = folder / ".market"
    return {"public_url": url.rstrip("/"), "tunnel_command": [
        str(private / "bin/ngrok"), "http", "8900", "--url", url.rstrip("/"),
        "--config", str(private / "ngrok.yml"), "--log", "stdout", "--log-format", "json"]}


def main():
    folder = Path(__file__).resolve().parent
    private = folder / ".market"
    private.mkdir(exist_ok=True)
    binary = private / "bin/ngrok"
    if not binary.is_file():
        raise SystemExit("ngrok executable is not installed in .market/bin/ngrok")
    print("Use your assigned HTTPS domain: https://dashboard.ngrok.com/domains")
    url = input("ngrok fixed HTTPS URL: ").strip()
    config = prepare_config(folder, url)
    fixed_path = private / "fixed-address.json"
    # Validate the public URL before handling the account credential.
    candidate = private / "fixed-address.pending.json"
    candidate.write_text(json.dumps(config, indent=2) + "\n")
    try:
        load_fixed_config(candidate)
    except (ValueError, TypeError):
        candidate.unlink(missing_ok=True)
        raise SystemExit("Enter an HTTPS origin without a path, query or credentials.")

    credential_path = private / "ngrok.yml"
    if not credential_path.exists():
        print("Agent authtoken: https://dashboard.ngrok.com/get-started/your-authtoken")
        token = getpass.getpass("ngrok authtoken (hidden; stored locally only): ").strip()
        if not token:
            candidate.unlink(missing_ok=True)
            raise SystemExit("No credential saved. Existing market runtime remains running.")
        fd = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write('version: "3"\nagent:\n  authtoken: ' + json.dumps(token)
                         + '\n  web_addr: "127.0.0.1:4041"\n')
        del token
    check = subprocess.run([str(binary), "config", "check", "--config", str(credential_path)],
                           capture_output=True, text=True)
    if check.returncode:
        candidate.unlink(missing_ok=True)
        raise SystemExit("Local ngrok config validation failed. No credential was printed.")
    candidate.replace(fixed_path)
    print("Local setup saved. Tell the coding agent setup is complete so it can verify and switch the market URL.")
    print("The existing tunnel has not been stopped.")


if __name__ == "__main__":
    main()
