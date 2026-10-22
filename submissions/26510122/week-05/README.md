# Week 05

## Part B LAB: Week 01 tools as an MCP server

`lab/tools_server.py` owns the three Week 01 tools. `lab/mcp_agent.py` keeps the
Week 01 agent loop but discovers tools with `tools/list` and executes them with
`tools/call`. No tool name is hardcoded in the host.

```bash
cd submissions/26510122/week-05
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# MCP client starts the server over stdio.
.venv/bin/python lab/smoke_client.py

# The same tools over Streamable HTTP.
.venv/bin/python lab/tools_server.py --http
.venv/bin/python lab/smoke_client.py --server http://127.0.0.1:8000/mcp

# Run the original model loop as an MCP host.
.venv/bin/python lab/mcp_agent.py
```

The server workspace defaults to `lab/`. Set `MCP_WORKSPACE` before starting
the server to expose a different working directory.

## Assignment: authenticated negotiation market

`market_server.py` is the Streamable HTTP MCP market. `market_host.py` discovers
its tools and uses `codex exec` to choose one move at a time. `run.py` starts the
server, opens negotiations, runs both required injection conditions, and resumes
from rows already present in `results.csv`.

```bash
# Re-run the four real authorization checks.
.venv/bin/python auth_checks.py

# Run all 24 required episodes (2 conditions x 3 repeats x 4 scenarios).
.venv/bin/python run.py --all --runs 3

# Run unit and submission checks.
.venv/bin/python -m unittest -v test_market.py
python3 ../../../scripts/check_week05.py .
```

The experiment uses the existing Codex CLI login with `gpt-5.6-luna`; it does
not read an API key from `.env`. See `REPORT.md` for the setup and results.
