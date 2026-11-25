# Tools the market agents choose from

Each tool is a folder: `manifest.json` (name, purpose, args as JSON Schema, evidence for why it exists) and
`tool.py` (reads JSON args on stdin, prints one JSON value). `agent_tools.py` finds them at every run, so a tool
Terra adds here is available to Luna at the next decision without a restart.
