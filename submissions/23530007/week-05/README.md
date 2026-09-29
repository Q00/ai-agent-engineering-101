# Week 05 — 23530007

Week-01 tools moved into an MCP server, and the week-04 negotiation moved onto an MCP market
server with Bearer tokens. Python 3.10+ and `mcp` v2 (`pip install -r requirements.txt`).
Results and interpretation are in [REPORT.md](REPORT.md).

## Status

| Piece | State |
|---|---|
| `market_server.py` (401, binding, turn order, token limit, injection) | Runs. `auth_checks.txt` is its real output. |
| `run_market.py` + `market_host.py` | Ran with real models. |
| `results.csv`, `logs/` | `prompt_inject`, `server_inject`: `claude-haiku-4-5-20251001`, 18 episodes each. `server`: `gpt-4.1-mini`, 18. `prompt`: 9 haiku + 9 gpt-4.1-mini. Crashed rows are kept with blank fields. |
| `REPORT.md` | Setup, results, week-04 comparison, interpretation, deviations from the spec, failed attempts. |
| `scripts/check_week05.py` | Passes on the committed files. |
| `server.py` + `host.py` (week-01 tools over MCP, stdio and `--http`) | Lab part. Checked with a scripted model only. |

**Deviations from the spec** (details in REPORT.md): the turn limit is 12 host runs, not 8 moves;
the optional conditions do not all use the same model as the required ones.

## Run

```bash
export MARKET_ADMIN_TOKEN=...      # any secret you choose; never commit it
export ANTHROPIC_API_KEY=...       # or OPENAI_API_KEY with AGENT_PROVIDER=openai; never commit it
python market_server.py            # terminal 1, 127.0.0.1:8001/mcp
python auth_checks.py | tee auth_checks.txt
python run_market.py               # terminal 2, the two required conditions x 3 repeats
AGENT_PROVIDER=openai python run_market.py --conditions prompt server   # how the optional runs were made
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
| `server.py`, `host.py` | Week-01 tools as an MCP server and the week-01 loop as its host |
