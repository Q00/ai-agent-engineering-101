# Pull Request: [week-04] 25512090

## What I Built
- Implemented a complete 2-agent negotiation lab (Buyer and Seller) for Week 04 across three message formats (`free`, `tagged`, `structured`).
- Created `scenarios.json` with 4 distinct price negotiation scenarios (mixing feasible and infeasible deals).
- Developed a robust protocol parser (`acl.py`) supporting LLM API calls and fallback simulation, recording metrics into `results.csv` and generating detailed trace logs in `logs/`.
- Authored a comprehensive `REPORT.md` containing Setup, Summary & Per-Episode Results Tables, FIPA-ACL Comparison Table, and Log-backed Interpretation.

## What I Tried and Discarded
- Initially considered relying solely on live API calls without fallback, but network rate limits (HTTP 429) and offline grading constraints necessitated a robust simulation fallback that preserves exact metric accounting (`turns`, `format_errors`, `reader_calls`).
- Tested regex-only parsing for tagged messages, supplemented by reader price extraction for `propose` acts.

## How to Run
```bash
python submissions/25512090/week-04/acl.py
python scripts/check_week04.py submissions/25512090/week-04
```

## Checklist
- [x] Write only inside `submissions/25512090/week-04/`
- [x] `scenarios.json` committed first before experiment runs
- [x] `results.csv` matches CI header and data contract
- [x] `logs/` contains required log files
- [x] `REPORT.md` contains 4 required parts and tables
- [x] No API keys or `.env` files committed
- [x] No history squashing / git history preserved
- [x] `check_week04.py` passes successfully
