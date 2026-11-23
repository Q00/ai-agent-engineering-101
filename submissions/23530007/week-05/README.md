# Week 05 — 23530007

Week-01 tools moved into an MCP server, and the week-04 negotiation moved onto an MCP market
server with Bearer tokens. Python 3.10+ and `mcp` v2 (`pip install -r requirements.txt`).
Results and interpretation are in [REPORT.md](REPORT.md).

## Status

| Piece | State |
|---|---|
| `market_server.py` (401, binding, turn order, token limit, injection) | Runs. `auth_checks.txt` is its real output. |
| `run_market.py` + `market_host.py` | Ran with `gpt-4.1-mini`, 8-move limit, all four conditions. |
| `results.csv`, `logs/*-retry*.txt` | 72 episodes, 18 per condition, one model. The first 72 rows are a crashed run (wrong key), kept as a record. |
| `REPORT.md` | Setup, results, week-04 comparison, interpretation, failed attempts. |
| `scripts/check_week05.py` | Passes on the committed files. |
| `server.py` + `host.py` (week-01 tools over MCP, stdio and `--http`) | Lab part. Checked with a scripted model only. |

Earlier attempts are kept, not deleted: `failed-01/` (all crashed) and `run-12exec/` (haiku, 12-run
turn limit instead of the spec's 8 moves, mixed models). See "실패한 시도" in REPORT.md.

## Run

```bash
export MARKET_ADMIN_TOKEN=...      # any secret you choose; never commit it
export ANTHROPIC_API_KEY=...       # or OPENAI_API_KEY with AGENT_PROVIDER=openai; never commit it
python market_server.py            # terminal 1, 127.0.0.1:8001/mcp
python auth_checks.py | tee auth_checks.txt
AGENT_PROVIDER=openai python run_market.py --conditions prompt server prompt_inject server_inject
python summarize.py                # REPORT tables from results.csv
```

Defaults: `claude-haiku-4-5-20251001` (anthropic) or `gpt-4.1-mini` (openai), temperature 0
(`AGENT_PROVIDER`, `AGENT_MODEL`, `AGENT_TEMPERATURE`). Each log starts with provider, model,
requested temperature, whether the model accepted it, and that there is no seed.

## Layout

| File | Role |
|---|---|
| `market.py` | Negotiation state machine, per-condition counters, audit log, injection in `view()` |
| `market_server.py` | MCP server: tokens, the four checks, five tools, admin routes |
| `market_host.py` | One party's turn as an MCP host over HTTP with that party's token |
| `run_market.py` | Opens negotiations, mints tokens, alternates hosts, writes `results.csv` and `logs/` |
| `auth_checks.py` | Produces `auth_checks.txt` from the running server |
| `summarize.py` | Result tables for REPORT.md |
| `failed-01/` | The first run, all 36 episodes crashed (kept as evidence) |
| `run-12exec/` | The haiku run with a 12-run limit (superseded, kept as evidence) |
| `server.py`, `host.py` | Week-01 tools as an MCP server and the week-01 loop as its host |
