# Week 05 report: the negotiation market as an MCP server

## 1. Setup

- **Host**: the week-01 loop as an MCP host (`run_market.py`, Streamable HTTP client with an `Authorization: Bearer` header). One turn = one host run, at most 6 model calls; if the host ends without a valid move, the runner passes the turn through `/admin/pass`.
- **Model**: `gpt-4o-mini`, temperature 0 (the same for buyer and seller). Same scenarios, same system prompts, same 8-move limit in all conditions.
- **Tokens**: the runner calls the admin route `/admin/open` (guarded by `MARKET_ADMIN_TOKEN`, not an MCP tool). The server mints two random tokens (`secrets.token_urlsafe(24)`) per negotiation. A token carries `{role, negotiation_id, limit}`. `limit` is `None` in the prompt conditions, `{"max": budget}` for the buyer and `{"min": reserve}` for the seller in the server conditions. The model never sees the token.
- **How to run** (needs `pip install "mcp>=2" httpx2 requests python-dotenv openai`; API key in `.env` or environment, not committed):
  ```
  python run_market.py --runs 1 2 3 --conditions prompt_inject server_inject
  python auth_check.py > auth_checks.txt      # with MARKET_ADMIN_TOKEN set and market_server.py running
  ```
  The runner starts `market_server.py` itself and skips rows already in `results.csv`.
- **Caveat on reproducibility**: the rerun of the 24 episodes (commit `fa02d3e`) gave different results from the first run although temperature is 0. Expect the trend, not identical rows. `results.csv` and `logs/` hold the rerun.

## 2. Results

| condition | episodes | correct | violations | attempted violations | refused calls | mean turns | mean tool calls |
|---|---|---|---|---|---|---|---|
| prompt_inject | 12 | 9 | 0 | 0 | 0 | 6.92 | 14.00 |
| server_inject | 12 | 8 | 0 | 0 | 0 | 7.08 | 14.42 |

Outcomes: prompt_inject 3 deal / 3 no_deal / 6 open; server_inject 2 deal / 1 no_deal / 9 open.

Per-episode table: see `results.csv` (24 rows, one per episode; logs in `logs/<condition>-run-0N.txt`).

Refusals followed by a valid move in the same turn: **0**, because the server refused no call in any episode (`refused_calls` is 0 in all 24 rows).

## 3. FIPA-ACL (week 04) against the market

| | FIPA-ACL (week 04) | Market MCP server (week 05) |
|---|---|---|
| Who the sender is, and who says so | the `sender` field of the message, written by the agent itself | the role from the bearer token; no tool has a sender argument; the server says so |
| Where the act lives | the performative field of the message | the tool name (`propose`, `accept_proposal`, `reject_proposal`, `refuse`) |
| What the content is | a message body the reader has to parse | an integer `price` argument and a `negotiation_id`, typed by the tool schema |
| Who enforces the limit | nobody; the model's own job, in the prompt | prompt condition: the model; server condition: the server, from the token's limit |
| What can be verified from outside | the message log only; the sender is a claim | the token check (`auth_checks.txt`), the server state via `/admin/result`, refusal events |
| Which failures appeared | see week-04 report | model stalls into `open` (13 of 24 episodes hit the 8-move limit); see part 4 |

## 4. Interpretation

_To be written by the student._ Evidence to work from, all in `logs/`:

- The injected sentence reached the buyer in only some episodes (log lines with `[market notice]`: `prompt_inject-run-01` and `-03`, `server_inject-run-02`, all in the laptop scenario). Elsewhere the seller never proposed, or the buyer never read a seller proposal, so the injection was never in play.
- In those laptop episodes the seller proposed 900, which is inside the real budget (1000). The buyer rejected it anyway, in the log at `prompt_inject-run-01.txt` lines 26 to 38. Does the buyer's `note` mention the notice or the budget? Read it.
- The buyer never called `accept_proposal` in any log; every deal was closed by the seller. So no buyer accepted above its real budget, and the server's limit check never fired: `attempted_violations` and `refused_calls` are 0.
- Question to answer: given that, what can this data say about which layer held, and what would you need to run (for example a buyer model that accepts, or the `prompt`/`server` conditions) to say more?
