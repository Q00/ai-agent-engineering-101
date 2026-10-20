# Week 07 SETUP steps 1 and 2

Completed on 2026-10-20, following the SETUP section of `week-07.html`.

## Step 1: Windows environment

- Official week-07 materials were fetched from `upstream/main` and merged into the `week-07` branch.
- `scripts/market_kit.py` and `scripts/trade_test_week06.py` are present.
- Created the repository-root `.venv` with `py -3.12 -m venv .venv`; its Python is 3.12.4. The default system `python` still points to 3.9, so activate this environment before running course commands.
- Installed the lecture packages, then applied this submission's `requirements.txt` to retain the working week-06 dependency versions: a2a-sdk 1.2.2, uvicorn 0.54.0, httpx 0.28.1 and pydantic 2.14.1. Added cryptography 50.0.2 for the market kit.
- `winget install --id Cloudflare.cloudflared --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity` failed because the winget CDN could not be reached (`InternetOpenUrl() failed`, `0x80072efd`).
- Instead, installed Cloudflare's official Windows amd64 executable, release 2026.10.2, in `.venv/Scripts/cloudflared.exe`. Its SHA-256 matched the GitHub release asset digest: `12c7c887f8aaab779aef1dfa9b60673530a5adb88ecc55dd6df8f56e0b70f199`. This executable is available when the repository-root venv is active; it is not a system-wide winget installation.
- Official download documentation: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python --version
python -m pip check
cloudflared --version
```

To recreate the Python dependencies after creating and activating the venv:

```powershell
python -m pip install -r submissions/26510121/week-07/requirements.txt
```

## Step 2: copy the working week-06 agents

Copied all 1,865 Git-tracked files from `submissions/26510121/week-06/` to `submissions/26510121/week-07/`, using the working week-06 baseline at `982dfbc`. Verified every copied file against its source by SHA-256: zero differences before adding week-07 setup records.

This includes the original logs, documents and `test-report.json` as an unchanged week-06 baseline. They describe week-06 runs, not new week-07 results. The copied `REPORT.md` is also still the week-06 report. Setup changes are recorded separately here and in `logs/setup/`.

Ignored local files were not copied: `.venv`, `.runtime`, caches and `.env`. No credentials or private keys were copied. The Python agents still require the existing OpenAI credential to be made available before a future live trade; setup verification makes no API calls.

`agent.json` keeps the existing seller and buyer commands. LAB 1's market buyer and LAB 2's receipt signatures have not been implemented in this setup step.

Validation: venv activation, dependency consistency, required package imports, kit help, seller help and buyer help. STEP 3's trade test and STEP 4's market join have not been run. Original logs were not edited.

Dependency installation, official cloudflared download and final environment verification outputs are saved under `logs/setup/`.
