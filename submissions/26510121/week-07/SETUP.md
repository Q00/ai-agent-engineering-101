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

## Steps 3 and 4: market connection (2026-10-20)

STEP 3's trade test was explicitly skipped at the user's request. The copied week-06 test report is not evidence of a week-07 test run.

Read and followed https://market.ourolabs.ai/agent.md using the existing agent directory and installed environment. Windows launch and stop scripts are in `agent-market/` inside this submission, keeping repository writes within the student's submission directory. Agent source code was not changed.

The user approved reuse of the existing week-06 OpenAI credential. `agent-market/join.ps1` reads it without printing it and passes it to the child process through its environment. It does not copy or write the credential to a file. The supplied market claim code is also passed through the environment and is not embedded in the scripts or logs.

At 19:39:15 KST, the kit confirmed both:

```text
[market] shop registered as seochanit, round 0
[market] buyer registered as seochanit, round 0
```

The public agent card returned HTTP 200 at:
https://hist-delays-situation-food.trycloudflare.com/.well-known/agent-card.json

The tunnel initially needed about one minute for DNS and HTTP access to become available. The kit then received its first market order. Registration is not a claim that the market test or Round 2 deal requirements have passed.

The first live order bought a chair for 210 within its budget of 225. The market recorded `ledger #192`, `verified True`; the kit verified the other shop's seller signature and added the buyer signature. This was a Round 0 trade, not a trade-test run. The next order had budget 160 and ended without a deal (`ledger #211`). Raw records are in `logs/market-kit.log` and the corresponding buyer logs.

- Leaderboard: https://market.ourolabs.ai/board
- Continuous kit log: `logs/market-kit.log`
- Per-session stdout and stderr: `logs/market-sessions/`
- Current PID and log paths: `.market/session.json` (ignored by Git)
- Local signing keys: `shop_key.pem` and `buyer_key.pem` (ignored by Git; retain them to keep the same identity)

The kit, seller and tunnel run in the background on this PC. The PC must stay on and connected to keep them available. No automatic startup service was installed.

Stop the current session from the repository root:

```powershell
.\submissions\26510121\week-07\agent-market\stop.ps1
```

To restart after stopping, provide the claim code locally through `MARKET_OWNER`, then run:

```powershell
.\submissions\26510121\week-07\agent-market\join.ps1
```

The launch script refuses to start a second kit while the recorded process is running. A restarted quick tunnel receives a new public URL. Keep the existing signing keys and nickname.

### Status after LAB 1 restart, 2026-10-20 20:25 KST

The original full shop session described above has ended. Subsequent quick-tunnel restarts did not obtain a public URL. Following the official `agent.md` fallback, the current session uses `--buyer-only`; the market confirmed `buyer registered as seochanit, round 0` at 20:25:35. The buyer is running; the shop is currently unreachable. Current session details remain in `.market/session.json`. LAB 1 implementation, test results and remaining R1 live checkpoints are documented in `LAB1.md`.

### Latest status: 2026-10-20 20:42 KST

The market is now R1. The new market buyer has completed live trades and posted accepted reviews. The shop has been restored through a separate Pinggy HTTPS tunnel after Cloudflare continued rejecting quick-tunnel requests with 429. Both shop and buyer were verified ONLINE by the market API at 20:42:19. Details and evidence are in `LAB1.md` and `logs/market-sessions/online-verification-20261020-204219.json`.

The free Pinggy session has a 60-minute timeout; staying online beyond that requires reconnecting and re-registering the new URL or using durable deployment. To restart the shop, stop it with `agent-market/stop.ps1 -ShopOnly`, then use `agent-market/join.ps1 -ShopOnly -TunnelProvider pinggy` with the claim code supplied locally via `MARKET_OWNER`. The buyer stays independent and continues running.
