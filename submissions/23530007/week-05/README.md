# Week 05 — 23530007

Week-01 tools moved into an MCP server, and the week-04 negotiation moved onto an MCP market
server with Bearer tokens. Python 3.10+ and `mcp` v2 (`pip install -r requirements.txt`).

## Status

| Piece | State |
|---|---|
| `server.py` + `host.py` (week-01 tools over MCP, stdio and `--http`) | Runs against mcp 2.x with a scripted model. Not run with a real model. |
| `market_server.py` (401, binding, turn order, token limit, injection) | Runs. `auth_checks.txt` is its real output. |
| `run_market.py` + `market_host.py` | Runs end to end against the live server with a scripted model. **Not run with a real model.** |
| `results.csv`, `logs/`, `REPORT.md` | **Not produced yet.** They need a real run and the analysis. |

## Run

```bash
export MARKET_ADMIN_TOKEN=...      # any secret you choose; never commit it
export ANTHROPIC_API_KEY=...       # never commit it
python market_server.py            # terminal 1, 127.0.0.1:8001/mcp
python auth_checks.py | tee auth_checks.txt
python run_market.py               # terminal 2, the two required conditions x 3 repeats
python run_market.py --conditions prompt server prompt_inject server_inject   # all four
```

Model `claude-haiku-4-5-20251001`, temperature 0 (`AGENT_MODEL`, `AGENT_TEMPERATURE`). Each run
logs model, requested temperature, whether the model accepted it, and that there is no seed
(scenario order is fixed, nothing is random).

## Layout

| File | Role |
|---|---|
| `market.py` | Negotiation state machine, per-condition counters, audit log, injection in `view()` |
| `market_server.py` | MCP server: tokens, the four checks, five tools, admin routes |
| `market_host.py` | One party's turn as an MCP host over HTTP with that party's token |
| `run_market.py` | Opens negotiations, mints tokens, alternates hosts, writes `results.csv` and `logs/` |
| `auth_checks.py` | Produces `auth_checks.txt` from the running server |
| `server.py`, `host.py` | Week-01 tools as an MCP server and the week-01 loop as its host |
