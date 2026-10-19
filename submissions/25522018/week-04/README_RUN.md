# Week 04 — Run on Windows PowerShell (GroqCloud)

- Provider: GroqCloud
- Model: `openai/gpt-oss-20b` (set `GROQ_MODEL` to change)
- 4 scenarios × 3 communication formats × 3 repeats = **36 LLM episodes**.
- Before rerunning, back up the previous `results.csv`, `REPORT.md`, and `logs` outside the repository and remove the old files from this working directory. Otherwise old HTTP 401 failure rows will be incorrectly skipped.
- Use the same model for all 36 episodes.

```powershell
# Run these from the repository root:
cd submissions/25522018/week-04
$secure = Read-Host 'Paste Groq API key' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $env:GROQ_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
Remove-Variable secure, ptr
$env:GROQ_MODEL = 'openai/gpt-oss-20b'
python run_week04.py
```

If transient API errors occur, **rerun the same command** after recovery; completed episodes are preserved in `results.csv`. Do not publish API keys or temporary backup copies.

From the repository root:

```powershell
python scripts/check_week04.py submissions/25522018/week-04
```
