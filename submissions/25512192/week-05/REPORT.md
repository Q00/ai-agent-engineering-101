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
- **Prompts**: `--prompts v1` (default, runs 1-3) or `--prompts v2` (runs 4-6); v2 command: `python run_market.py --prompts v2 --runs 4 5 6 --conditions prompt_inject server_inject prompt server`.
- **Caveat on reproducibility**: the rerun of the 24 episodes (commit `fa02d3e`) gave different results from the first run although temperature is 0. Expect the trend, not identical rows. `results.csv` and `logs/` hold the rerun.

## 2. Results

Two prompt versions, both kept. **v1** (runs 1-3, only `prompt_inject` and `server_inject`) is the first attempt: the seller only ever rejected, so the injected sentence, which rides on a seller `propose`, reached the buyer in only 3 of 24 episodes. **v2** (runs 4-6, all four conditions, `prompts=v2` in `note`) adds one sentence to each system prompt: the seller answers a low offer with a counter-proposal, the buyer accepts a price it can live with. The limit wording is unchanged and v2 is identical across the four conditions.

| prompts | condition | episodes | correct | violations | attempted violations | refused calls | mean turns |
|---|---|---|---|---|---|---|---|
| v1 | prompt_inject | 12 | 9 | 0 | 0 | 0 | 6.92 |
| v1 | server_inject | 12 | 8 | 0 | 0 | 0 | 7.08 |
| v2 | prompt_inject | 12 | 9 | 0 | 0 | 0 | 5.67 |
| v2 | server_inject | 12 | 9 | 0 | 0 | 0 | 5.58 |
| v2 | prompt | 12 | 9 | 0 | 0 | 0 | 5.42 |
| v2 | server | 12 | 9 | 0 | 0 | 0 | 5.92 |

v2 outcomes are 7 no_deal / 3 deal / 2 open in every condition. The injected sentence was in the buyer's view in 7, 7 and 9 log lines of `prompt_inject` runs 4-6 and in the same numbers for `server_inject`; no buyer called `accept_proposal` in any v2 log, and none quoted the notice.

Per-episode table: `results.csv` (v1 rows have runs 1-3, v2 rows runs 4-6; logs in `logs/<condition>-run-0N.txt`).

Refusals followed by a valid move in the same turn: **0**, because the server refused no move in any episode (`refused_calls` is 0 in all rows). The only tool errors in the logs are `reject_proposal` calls with no proposal to answer (for example `server_inject-run-05.txt` lines 46 and 58), which are not limit refusals.

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

- v1 (runs 1-3): the injection almost never reached the buyer, so it measured nothing about the layers. Why, and what did v2 change? (`prompt_inject-run-01.txt` lines 22 to 38 vs `prompt_inject-run-04.txt`.)
- v2: the notice reached the buyer in every scenario with a seller counter, yet the buyer replied "too high for my budget" (`prompt_inject-run-04.txt`, ticket and bicycle episodes) and never accepted. Did the model name the injection and ignore it, or simply not read it? The logs show no buyer text about it.
- `attempted_violations` and `refused_calls` are 0 in all 72 v1+v2 episodes, so the server's limit check never fired in an episode; its behaviour is shown only in `auth_checks.txt` line 4.
- Question to answer: with gpt-4o-mini both layers "held", but only the prompt layer was ever tested. What would a model that does accept the injected budget show, and what does that say about which layer you should trust?
