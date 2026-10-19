# Week 06 — Windows setup and real A2A verification

Run **from the repository root** (`D:\ai-agent-engineering-101-q00`) in PowerShell.

## 1. Keep coursework separate

```powershell
git switch main
git fetch origin
git switch -c week-06
```

Skip branch creation if you already have a local `week-06` branch; instead use `git switch week-06`. Do not force-reset branches containing your previous work. The instructor trade-test files must exist under `scripts/` (fetch from upstream if necessary).

## 2. Install Python packages in a new venv

Recommended Python 3.12 if available (Python 3.14 may work too):

```powershell
py -3.12 -m venv .venv-week06
.\.venv-week06\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install "a2a-sdk[http-server]==1.2.2" uvicorn httpx
python -c "import a2a, httpx, uvicorn; print('Week 6 dependencies ready')"
```

If only `py --version` works and Python 3.12 is not installed, install Python 3.12 or try `py -m venv .venv-week06`; all commands in `agent.json` use the active virtual environment's `python` executable.

## 3. Configure Groq key securely in the CURRENT PowerShell terminal

```powershell
$secureKey = Read-Host "Groq API key" -AsSecureString
$env:GROQ_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
Remove-Variable secureKey
$env:GROQ_MODEL = "openai/gpt-oss-20b"
python -c "import os; print('Groq configured:', bool(os.getenv('GROQ_API_KEY')))"
```

`True` only indicates that a key is present, not that it is valid. Never copy a key into a `.py`, `.md`, or `.json` file.

## 4. Verify the real, current instructor kit

From repository root:

```powershell
python scripts/trade_test_week06.py submissions/25522018/week-06
python scripts/check_week06.py submissions/25522018/week-06
```

The first command starts a local seller, runs actual buyer and seller A2A interactions with Groq API calls, and writes `test-report.json` and real log files. **Do not invent or manually replace these outputs.** The exact pass count depends on the instructor's script; the current version targets 21 results.

If any test fails, inspect `logs/trade-test.log`, `logs/seller-under-test.log`, and individual `logs/buyer-under-test-*.log` in the submission folder. Re-running the trade test will overwrite those files, so copy failed logs to `logs/debug-before-fix/` if you want to quote evidence in REPORT.md.

## 5. Required challenges

From submission folder, after changing directories:

```powershell
cd submissions/25522018/week-06
python catalog.py "something wooden to keep about 40 books, not taller than my desk"
python challenge_catalog_demo.py 2>&1 | Tee-Object logs/challenge-1/console.log
python challenge_multi_shop.py --spawn-demo-shops 20 --total-budget 750 2>&1 | Tee-Object logs/challenge-2/console.log
cd ../../..
```

The catalog has 1,763 products; it uses local lexical retrieval before sending a shortlist to the Groq model, which chooses an SKU. The multi-shop challenge starts 20 local copies of the shop, reads their A2A Agent Cards, has Groq select a shop for each item, and negotiates across several shops concurrently. Each purchase has a reserved ceiling, enforced in code against the shared total of 750, with a ledger and receipt verification. The challenge log folder is created automatically.

**Important:** PowerShell `Tee-Object` writes the actual run output; nothing is pre-filled or simulated. Both challenges can fail due to model behavior or limits, in which case keep the log and report the real limitation rather than claiming success. The multi-shop demo may use considerable memory; if your computer cannot run 20 seller processes at once, use your own externally running shop directory instead.

## 6. Review and push only your Week 6 files

After all checks pass, edit `REPORT.md` with the observed results, logged failure lines and fixes, and challenge results. Then:

```powershell
python scripts/check_week06.py submissions/25522018/week-06
git add submissions/25522018/week-06
git diff --cached --check
git diff --cached --stat
git commit -m "week06: implement A2A buyer seller and shopping challenges"
git push -u origin week-06
```

If the push attempts to update `.github/workflows/`, the branch is not clean; do not force-push. Make sure the GitHub PR to `Q00:main` changes only `submissions/25522018/week-06/`.
