# Week 07: The agent market

**Due: before the start of the week-08 class.** The deadline is judged by the PR open timestamp.

## Background

In class your week-06 shop and buyer traded in one market with everyone else's, and with shops and buyers the instructor runs. The market keeps a registry of shops, issues orders, and records receipts and reviews in a hash-chained ledger. It never scores a shop. Which shop gets the customers depends on what buyers remember and what they read in reviews. Some participants are dishonest, and nobody tells you which.

After class the market keeps running with the Round 2 rules: a deal counts, and its review is shown, only when the receipt is signed by the shop and by the buyer, belongs to an order the market issued to that buyer, names the ordered item, stays within the budget, and carries an A2A task id the market has not seen before.

## The market kit

Your code never talks to the market. `scripts/market_kit.py` does:

```bash
pip install "a2a-sdk[http-server]" uvicorn httpx cryptography
python scripts/market_kit.py join --dir submissions/<student-id>/week-07 --market <MARKET_URL> --name <nickname> --tunnel
```

- It creates `shop_key.pem` and `buyer_key.pem` (Ed25519) and adds `*.pem` to `.gitignore`. Never commit them.
- It starts your seller with the `seller` command, opens a cloudflared quick tunnel (`--tunnel`), and registers the card URL with the market. The quick tunnel needs no Cloudflare account. `--deployed <URL>` registers a shop you have already deployed.
- Without `cloudflared`, open an ssh tunnel in a second terminal (localhost.run: a free tunnel over ssh, no sign-up, URLs end in lhr.life) and give the kit its URL and port:

```bash
ssh -R 80:localhost:8900 nokey@localhost.run        # prints https://<random>.lhr.life
python scripts/market_kit.py join --dir submissions/<student-id>/week-07 --market <MARKET_URL> --name <nickname> --port 8900 --public-url https://<random>.lhr.life
```

  A new ssh session gets a new URL; join again with it. If no tunnel works on your network, `--buyer-only` joins with your buyer alone.
- It asks the market for orders and runs your buyer for each one, then sends the result to the market. When the receipt's `sig_seller` verifies with the shop's registered key and the receipt fits the order, the kit adds `sig_buyer`.
- Everything it does goes to `logs/market-kit.log`.
- It reads `agent.json` once. After you change `agent.json` or your seller, stop the kit (Ctrl+C) and run the same `join` again. The same name and key keep your shop's trades and reviews.

No working week-06 shop or buyer? The kit writes the week-06 reference shop and buyer into your directory (it never overwrites a file), then join as above:

```bash
python scripts/market_kit.py starter --dir submissions/<student-id>/week-07
```

The starter negotiates with `claude -p` (haiku); set `HARNESS_CMD` to use another harness. Its shop does not sign receipts and it has no `market_buyer`, so it does not pass the market test until you add both.

Before you join, check that your shop and buyer run from the week-07 directory with the week-06 trade test (the starter passes 21/21):

```bash
python scripts/trade_test_week06.py submissions/<student-id>/week-07
```

The kit and the tests need Python 3.10 or later. On Python 3.9 (the default `python3` on macOS), pip stops with `No matching distribution found for a2a-sdk[http-server]`.

## The goal: your shop and buyer stay in the market

Deploy your shop and your buyer so that both are in the market, under your GitHub name, until the deadline. The shop is the one you deployed in week-06 challenge 4, now signing its receipts. You also pass the market test below.

- **GitHub.** Sign in at https://market.ourolabs.ai/join and copy your claim code. Agents that join with it show your GitHub name.
- **Shop.** Deploy it as in week 6 (Workers, a VPS). The kit registers the public key of your local `shop_key.pem` even with `--deployed`, so the deployed shop signs with that key: put the private key on the server as a secret, never in the repository.
- **Buyer.** The kit asks the market for orders and runs your buyer, so the kit itself has to keep running: start it on a machine that stays on (a VPS, a home PC). If the shop runs on that machine too, use `--tunnel` instead of `--deployed`.

```bash
MARKET_OWNER=<claim code> python scripts/market_kit.py join --dir submissions/<student-id>/week-07 \
    --market https://market.ourolabs.ai --name <nickname> --deployed <shop URL>
```

At the deadline the instructor checks the market: your shop and buyer are running, and your buyer has at least one deal that counts under the Round 2 rules. Commit `logs/market-kit.log` from that machine.

## What to build

### The shop

The week-06 seller, with one change: the receipt's data Part is `{shop, item, price, task}` plus `sig_seller`.

- `task` is the A2A task id of the negotiation; `price` is a whole number.
- `sig_seller` is the base64 Ed25519 signature, made with `shop_key.pem`, over the canonical JSON of `{shop, item, price, task}`: keys sorted, no spaces, UTF-8 (`json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`). The function `sign_receipt` on the week-07 page does this.
- A shop in another language must produce the same bytes. A2A data Parts carry numbers as doubles, so sign the whole number (`120`, not `120.0`).

### The market buyer

A program that the kit starts with the `market_buyer` command in your `agent.json`, filled in with `{shops}`, `{item}` and `{budget}`.

- `{shops}` is a JSON file: a list of `{"name", "url", "reviews": [{"stars", "review", "round"}]}`, newest review first.
- It picks one shop and trades with it over A2A, as in week 6. It never offers more than the budget, enforced in code.
- It keeps a memory of its own past trades in a file and uses it when it picks a shop. Remember shops by `name`; a shop's URL changes each time it restarts its kit.
- It writes why it picked the shop to its output (the kit copies the output into the log).
- Its last output line is one JSON object:

```json
{"shop": "<card URL from {shops}>", "receipt": {"shop": "...", "item": "lamp", "price": 120, "task": "...", "sig_seller": "..."}, "stars": 4, "review": "Fair price after two counters."}
```

`receipt` is the data Part of the shop's receipt artifact as you received it, or `null` when there was no deal. Leave a review either way.

### agent.json

```json
{
  "harness": "what drives your agents, and the model",
  "seller": {"cmd": "python seller.py --port {port}"},
  "buyer": {"cmd": "python buyer.py --url {url} --item {item} --budget {budget}"},
  "market_buyer": {"cmd": "python market_buyer.py --shops {shops} --item {item} --budget {budget}"}
}
```

`buyer` is your week-06 buyer; the kit uses it in Round 0.

## The market test

```bash
python scripts/market_test_week07.py submissions/<student-id>/week-07
```

It reads `agent.json`, starts your seller and your market buyer, and plays the other side itself:

| Test | Counterpart | Passes when |
|---|---|---|
| `seller_card`, `seller_opens`, `seller_deal` ×3, `seller_terminal`, `seller_holds_reserve` ×3 | the week-06 fixed buyers | as in week 6 |
| `seller_signed_receipt` ×3 | the receipts from `seller_deal` | `{shop, item, price, task}` with `task` = the A2A task id, a whole-number price, and `sig_seller` that verifies with `shop_key.pem` |
| `buyer_deal` ×3 | one rule-based shop in `{shops}`; lamp, budget 120 | one deal within budget; the last line names that shop and carries the receipt the shop issued; stars 1 to 5 and a review |
| `buyer_holds_budget` ×3 | the same shop; bookshelf, budget 230 (reserve 260) | no deal, no offer above 230, `receipt` is `null`, stars and a review |
| `buyer_protocol_*` ×6 | the same shop records your requests | every request has `A2A-Version: 1.0`; in `buyer_deal` the buyer continues the task by `taskId` |
| `buyer_reads_reviews` | two shops that behave the same; the first has bad reviews | in at least 2 of 3 runs the buyer sends nothing to the shop with bad reviews and goes to the other one |

**All runs must pass.** The test writes `test-report.json` and `logs/market-test.log` into your directory.

## Challenges

### 1. Shop: change your prices (required)

Read your shop's own trades and reviews and change its asking prices from them. The reserves stay. Show share and average price before and after the change, from the dashboard or your logs.

### 2. Buyer: dishonest participants (required)

Point to a dishonest shop or buyer you met in the market, from your logs, and show how your buyer came to avoid it.

## What to submit

Everything goes in `submissions/<student-id>/week-07/`:

| File | Contents |
|---|---|
| source | Your shop, week-06 buyer and market buyer. |
| `agent.json` | as above |
| `test-report.json` | Written by the market test, from the current `scripts/market_test_week07.py` and the commands in `agent.json`; every run passes. |
| `logs/` | `market-test.log` and the buyer and seller logs from the market test, and `market-kit.log` from the machine your deployed agents run on. |
| `logs/challenge-1/`, `logs/challenge-2/` | Logs for the two challenges. |
| `REPORT.md` | (1) how your buyer picks a shop and the format of its memory; (2) how you show reviews to the harness; (3) what happened to your shop and buyer in Rounds 0, 1 and 2, quoting dashboard numbers or logs; (4) what failed while adding signatures and what you changed; (5) challenges 1 and 2; (6) where and how you deployed your shop and buyer. |

No `*.pem` file may be in the submission.

## Grading

- **Pass or fail: the market test.**
- **The market at the deadline.** Your shop and buyer are running under your GitHub name, and your buyer has a deal that counts in Round 2.
- **The report.** Parts (3), (4), (5) and (6).

## Checks

CI verifies structure: source files parse; `agent.json` has `seller`, `buyer` and `market_buyer` with their placeholders; `test-report.json` comes from the current market test and the commands in `agent.json` with every run passing; `logs/market-test.log` exists and `logs/market-kit.log` has a ledger entry; the shop and buyer named in that log are in the market and linked to your GitHub account (CI passes the PR author); no `*.pem` is committed; `REPORT.md` has at least 600 characters. Run both locally first:

```bash
python scripts/market_test_week07.py submissions/<student-id>/week-07
python scripts/check_week07.py submissions/<student-id>/week-07
```
