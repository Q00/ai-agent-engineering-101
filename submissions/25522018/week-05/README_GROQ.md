# Week 05 — Run with GroqCloud

The Week 05 host supports two providers: OpenRouter (default) and GroqCloud.
Use **one provider and one model for all 24 evaluated episodes**.

## First-time setup

1. Create a GroqCloud API key at https://console.groq.com/keys. Never put it in source files, logs, or Git.
2. Keep `market_server.py` running in Terminal 1 with a `MARKET_ADMIN_KEY` identical to Terminal 2.
3. In Terminal 2 (inside `submissions/25522018/week-05`), set:

```bash
export MARKET_ADMIN_KEY="$(cat /tmp/week05_admin_key)"
export GROQ_API_KEY='PASTE_NEW_GROQ_KEY_LOCALLY'
export LLM_PROVIDER=groq
export GROQ_MODEL='openai/gpt-oss-20b'
export LLM_TIMEOUT=120
export LLM_RETRIES=3
python3 -c "import os; print('Groq key configured:', bool(os.getenv('GROQ_API_KEY')))"
python3 check_auth.py
python3 run_week05.py --mode llm --repeats 3
```

The runner automatically backs up `results.csv` if it belongs to a different model/provider, then saves each completed Groq episode immediately. Rerun **the same** command to resume; do not use `--fresh` unless deliberately resetting all experiments. The previous OpenRouter six episodes remain in a timestamped `results_backup_*.csv`, which should **not** be committed.

## Verify

From the week-05 directory:

```bash
python3 - <<'PY'
import csv
rows=list(csv.DictReader(open('results.csv')))
print('Episodes:', len(rows))
print('Groq episodes:', sum(r['note'].startswith('llm; provider=groq; model=openai/gpt-oss-20b;') for r in rows))
PY
cd ../../..
python3 scripts/check_week05.py submissions/25522018/week-05
```

Note that structural checker success alone does not establish that all experiments are genuine. Update `REPORT.md` with measured outcomes and provider/model, and clearly distinguish Groq experimental results from scripted and prior OpenRouter runs. Groq API rate limits apply, including requests/tokens per minute and per day.
