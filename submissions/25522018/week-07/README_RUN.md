# Week 07 — Windows PowerShell setup

This package is derived from the previously prepared Week 06 GroqCloud source. It is a starter implementation, **not a claimed live passing test**. All actual tests and market transactions must be executed on your Windows computer.

## 1. Git branch and files

From the repository root, check that the tree is clean. Do not reset or overwrite your Week-04/05/06 work.

```powershell
cd D:\ai-agent-engineering-101-q00
git status --short
git fetch upstream
git switch main
git pull --ff-only origin main
git switch -c week-07
```

If your fork's `main` is behind `upstream/main`, do not merge unrelated changes into a PR branch. Fetch upstream and obtain the kit scripts as described below. The official PR must change ONLY `submissions/25522018/week-07/`.

Unzip the starter at repository root (merges into week-07 only).

## 2. Dependencies (use existing Week 06 environment)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv-week06\Scripts\Activate.ps1
python -m pip install 'a2a-sdk[http-server]' uvicorn httpx cryptography
```

Configure `GROQ_API_KEY` privately in the current PowerShell terminal; do not commit it. Configure `GROQ_MODEL=openai/gpt-oss-20b` to match Week 06.

## 3. Instructor's scripts

The scripts belong to `Q00:main`, not your homework directory. Once upstream has been configured:

```powershell
git fetch upstream
git restore --source=upstream/main --worktree -- scripts/trade_test_week06.py scripts/market_test_week07.py scripts/market_kit.py
```

Do not add these instructor scripts to your Week 07 PR. To confirm:

```powershell
Test-Path scripts/market_test_week07.py
Test-Path scripts/market_kit.py
```

## 4. Verify real A2A behavior

```powershell
python scripts/trade_test_week06.py submissions/25522018/week-07
python scripts/market_test_week07.py submissions/25522018/week-07
```

Target of instructor Week 07 market test: **25/25**. The test writes `test-report.json` and `logs/market-test.log`. It also generates a private `shop_key.pem` in this folder. Do NOT push the PEM or private `.market/` files to GitHub.

Use the generated log and real tests when editing REPORT.md.

## 5. Join instructor's live class market

Get the actual **market URL from your instructor or class**; it is not published in the course page. Install cloudflared if needed:

```powershell
winget install --id Cloudflare.cloudflared
cloudflared --version
python scripts/market_kit.py join --dir submissions/25522018/week-07 --market YOUR_CLASS_MARKET_URL --name 'Raksmey A2A Shop' --tunnel
```

The kit remains running for the always-on market while the terminal is open and the Windows PC is awake. The receipt `shop` name defaults to `Raksmey A2A Shop` and must match the name you register. If you use a different nickname, configure `MARKET_SHOP_NAME` to match before starting the kit.

Round 0 uses `buyer.py`; Round 1 reads reviews and memory with `market_buyer.py`; Round 2 verifies Ed25519 signatures in receipts. To move between rounds after the lecture instructions, stop with Ctrl+C and rerun the same join command.

## 6. Submit after verifying

Review REPORT.md, then:

```powershell
git add submissions/25522018/week-07
git diff --cached --name-only
git diff --cached --check
git commit -m 'week07: market reputation buyer and signed A2A receipts'
git push -u origin week-07
```

Use title `[week-07] 25522018` for the GitHub PR, and check the Files changed and Checks tabs.
