# Setup and execution

## Environment

- Python 3.12+
- `uv sync`
- A private file containing `OPENAI_API_KEY=...`; it is never copied into this repository.
- Frozen model: `gpt-4.1-nano-2025-04-14`, temperature 0.

## Validate without API calls

```bash
./run.py --dry-run
./run.py --all-conditions --dry-run
python scripts/check_week05.py submissions/25620027/week-05
```

The structural checker will continue to report missing repetitions until the live matrix has been completed.

## Run the required 36 episodes

```bash
./run.py --allow-paid --env-file /absolute/path/to/private.env
```

The command starts a local Streamable HTTP MCP server, captures the four authorization checks, runs every missing episode, and shuts the server down. Completed `(run, condition, scenario)` keys in `results.csv` are skipped safely when the command is resumed.

## Run all 72 episodes

```bash
./run.py --allow-paid --all-conditions --env-file /absolute/path/to/private.env
```

## Run the separate 18-episode liveness follow-up

```bash
./run.py --liveness-extension --dry-run
./run.py --liveness-extension --allow-paid --env-file /absolute/path/to/private.env
```

This mode selects only deal-possible scenarios and writes all results, events, authorization checks, and logs under `extension/liveness/`. It never resumes from or overwrites the required `results.csv`. Both `OPENAI_API_KEY=...` and `export OPENAI_API_KEY=...` private env-file syntax are accepted.

Runtime secrets are generated per process. The MCP host holds each bearer token in an HTTP header; tokens are not written to logs or passed to the model.
