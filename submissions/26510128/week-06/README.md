# Week 06 A2A agents — 26510128

This directory contains an A2A v1.0 JSON-RPC seller and buyer, both driven by a custom Python OpenAI-compatible decision-loop harness using local Ollama `qwen3:4b-instruct` (temperature `0.2`). The code validates reserve and budget bounds even if the model suggests an unsafe action.

- `seller.py`: Agent Card, JSON-RPC A2A task lifecycle, model-chosen asks/counteroffers/acceptance, receipt artifacts.
- `buyer.py` and `a2a_client.py`: Agent Card discovery, `A2A-Version: 1.0`, `taskId` continuity, model-chosen buyer decisions within budget.
- `model_harness.py`: local OpenAI-compatible inference, JSON parsing and action validation.
- `catalog.json`, `catalog_search.py`, `challenge_1.py`: 2,000 items with search shortlist of six, chosen by model; logs in `logs/challenge-1/`.
- `multi_shopper.py`, `challenge_2.py`: 20 Agent Cards, specialist-skill search, three concurrent negotiations, state and ledger tracking; logs in `logs/challenge-2/`.
- `REPORT.md`: actual test results, decisions/prompt contracts, enforcement, and failure-to-improvement evidence.

## Setup (Windows PowerShell)

From `submissions/26510128/week-06`:

```powershell
python -m pip install -r requirements.txt
$env:OPENAI_BASE_URL="http://localhost:11434/v1"
$env:OPENAI_API_KEY="ollama"
$env:AGENT_MODEL="qwen3:4b-instruct"
$env:AGENT_TEMPERATURE="0.2"
$env:AGENT_MAX_TOKENS="300"
```

Ollama must be running locally with the specified model installed. The `ollama` CLI need not be on `PATH` if the local API is reachable at `localhost:11434`.

Run from the **repository root**:

```powershell
python scripts/trade_test_week06.py submissions/26510128/week-06
python scripts/check_week06.py submissions/26510128/week-06
```

Run the challenges from the **submission directory**:

```powershell
python challenge_1.py
python challenge_2.py
```

The official test creates `test-report.json` and the `logs/trade-test.log` console evidence. Do not modify generated reports or logs to turn failures into passes. The challenge scripts create their own logs. Maintain the original first-run logs before retrying a challenge: commit attempts and corrections separately without squashing.

## Protocol conventions

A2A defines Agent Cards, `SendMessage`, task states, task IDs, and artifacts. The following **business semantics are application-specific**, not defined by A2A: the buyer sends a whole-number price in text; the seller's last number in an input-required status message denotes its ask; and a receipt data Part contains a numeric `price`. Seller and buyer policy clamps unsafe numeric actions to their respective private bounds.
