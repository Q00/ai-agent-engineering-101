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

The assignment market server will be implemented separately from this lab.
