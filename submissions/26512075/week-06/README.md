# Week 06 A2A negotiation agents

The seller is a Python A2A v1.0 JSON-RPC server. The buyer and shopping-list buyer are TypeScript programs using the A2A SDK. Each negotiation decision calls a Python model harness. The default is Codex CLI with gpt-6.1-sol. The deterministic harness/example_decisions.py is a development fixture, not a submission harness.

## WSL setup: Python, Node.js and npm

Open the **WSL terminal** in this project directory. Check both executables first:

```bash
node --version
npm --version
```

This project's `package.json` requires Node.js 20 or newer. If those commands work and Node meets that requirement, **skip nvm and Homebrew**. Your existing npm 11.19.0 can run the commands below. There is no global TypeScript or `a2a` executable to install for this project.

Install Python dependencies for the seller and npm dependencies for the TypeScript buyer:

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

npm install
npm install @a2a-js/sdk
npm install -D typescript tsx @types/node
npx tsc --version
npx tsc --init   # only if tsconfig.json is missing
npm run build    # writes dist/
```

`npm ci` installs the versions in `package-lock.json` locally under `node_modules/`. `npm run check` type-checks the TypeScript source, and `npm run build` generates the `dist/*.js` files used by `agent.json`. Activate the Python virtual environment in each new terminal before running the seller. For the real model run, also check that your authenticated Codex CLI is available with `codex --version`.

Codex (once per machine):

```bash
npm install -g @openai/codex
# API key auth, not ChatGPT login
set -a && source .env && set +a
printenv OPENAI_API_KEY | codex login --with-api-key
```

`.env` (do not commit):

```bash
OPENAI_API_KEY= # urs
CODEX_API_KEY= # urs
```

To check the A2A connection locally without a model login, use two WSL terminals after installation. In the first:

```bash
DECISION_MODULE=harness.example_decisions npm run seller -- --port 8080
```

In the second, from the same project directory:

```bash
curl http://127.0.0.1:8080/.well-known/agent-card.json
DECISION_MODULE=harness.example_decisions npm run buyer -- \
  --url http://127.0.0.1:8080 --item lamp --budget 120
```

This local fixture checks the protocol and code guards only. Unset `DECISION_MODULE` when using the real Codex harness and generating submission logs.

agent.json runs `python seller.py --port {port}` and `node dist/buyer.js --url {url} --item {item} --budget {budget}` with this directory as working directory. To try them manually, start the seller on port 8080 and point the buyer to http://127.0.0.1:8080.


## Source layout

| Path | Purpose |
| --- | --- |
| seller.py, app.py, storage.py | A2A server, hosted ASGI entry point, optional Redis task and negotiation state |
| src/buyer.ts, src/shopping-list.ts | Single-item buyer and shopping-list buyer |
| harness/model_decisions.py, harness/decide.py | Isolated non-interactive model calls and JSON bridge |
| catalog/items.jsonl, catalog.py | 3,000 generated products plus three basic products, bounded retrieval |
| scripts/run_challenge_*.py | Challenge demonstrations and logs |
| harness/example_decisions.py | Development fixture only |

The seller serves /.well-known/agent-card.json and its JSON-RPC endpoint at /. The card lists JSONRPC protocol version 1.0. The buyer SDK sends A2A-Version: 1.0 and follows up using the same taskId.

## Trade test

From the repository root:

```bash
python scripts/trade_test_week06.py submissions/<student-id>/week-06
```

The test reads `agent.json`, starts the seller and the buyer, and plays the other side. Current result: `21/21 passed`.

## Challenges

With the real harness ready, run from this directory:

```bash
python scripts/run_challenge_1.py
python scripts/run_challenge_2.py --shops 20 --budget 450
python scripts/run_challenge_3.py
```

These write logs/challenge-1/, logs/challenge-2/, and logs/challenge-3/. Challenge 2 launches 20 copies of our seller with category-specific cards. To mix in classmates' shops, provide a directory JSON array of `{"id":"shop-id","url":"http://host:port"}` and a list JSON array of `{"item":"lamp"}` to `node dist/shopping-list.js --directory ... --list ... --budget ...`. The buyer discovers cards, selects shops by skills, opens candidate tasks concurrently, and records each purchase at most once.

For a local transport check without a CLI account, prefix commands with DECISION_MODULE=harness.example_decisions. This checks protocol and guards, not model reliability.

## Hosting option

app.py exports the ASGI app. Set P_BASE_URL(PUBLIC_BASE_URL) to its HTTPS origin and REDIS_URL for persistent tasks and negotiation state. A headless server needs HARNESS_BACKEND=openai, OPENAI_MODEL and OPENAI_API_KEY for API-backed inference. Check the public card and a complete trade before setting `"deployed":"https://..."` in agent.json.