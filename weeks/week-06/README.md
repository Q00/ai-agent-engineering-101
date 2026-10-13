# Week 06: Your harness as a buyer and a seller

**Due: before the start of the week-07 class.** The deadline is judged by the PR open timestamp.

## Background

In the lab you started A2A servers and bought a lamp from a rule-based shop with the a2a CLI: one task, `TASK_STATE_INPUT_REQUIRED` (`input-required` in the CLI) while the price was open, `TASK_STATE_COMPLETED` with a receipt artifact at the end.

A2A exists so that agents built on different stacks can hand work to each other without knowing what runs inside. This week you put the harness you already use (Claude Code, Codex, omp, your own week-01 or week-02 loop, ...) behind A2A twice: as a seller that negotiates, and as a buyer that shops. Your code only speaks A2A; the harness makes every decision.

## No starter this week

There is no `starter/` directory and no template. You write both agents. What is fixed is the trade test that your agents must pass and the submission contract below.

## What to build

### The seller

- An A2A v1.0 server with the JSON-RPC binding and an Agent Card at `/.well-known/agent-card.json` (`supportedInterfaces[0]`: `protocolBinding` `JSONRPC`, `protocolVersion` `1.0`, `url`).
- It sells `lamp`, `chair` and `bookshelf`. The lowest prices it may accept (reserves) are 100, 180 and 260.
- A first message that names an item (for example `I want to buy a lamp`) gets `TASK_STATE_INPUT_REQUIRED` and a message that contains the asking price as a number.
- Follow-up messages on the same `taskId` carry the buyer's offer as a number. The seller answers with a counteroffer (`TASK_STATE_INPUT_REQUIRED`), closes the deal (`TASK_STATE_COMPLETED` with an artifact whose data Part has a numeric `price`), or ends the negotiation (`TASK_STATE_REJECTED`).
- The trade test reads the asking price as the last number in each `TASK_STATE_INPUT_REQUIRED` message. The seller never asks below the reserve and never sells below it, even when no deal follows. A message to a finished task gets an error.
- Your harness decides the asking price, the counteroffers, and when to accept or reject.

The lab's `seller_agent.py` shows the A2A side; replace its rules with calls to your harness.

### The buyer

- A program that the trade test starts with the command in your `agent.json`, filled in with `{url}` (the seller's base URL), `{item}` and `{budget}`.
- It reads the seller's Agent Card, sends every request with the `A2A-Version: 1.0` header, and continues the negotiation with the task's `taskId`.
- It buys within the budget when the seller can come down to it, and walks away (`CancelTask`, or simply stops) when it cannot. It never pays more than the budget.
- It exits when it is done, within 300 seconds.
- Your harness decides every offer and when to walk away.

### The harness

Any harness that you can call from code works. Two non-interactive commands that take the prompt on stdin, checked on 2026-10-13:

```bash
claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
codex exec --skip-git-repo-check -
```

For other harnesses, use their non-interactive mode. Run the harness in an empty directory so it does not pick up a project's instructions. How the harness answers (free text, JSON), what goes into the prompt, and whether the limits (reserve, budget) live only in the prompt or are also enforced in code are your decisions. State them in the report.

## The trade test

```bash
python scripts/trade_test_week06.py submissions/<student-id>/week-06
```

It reads `agent.json`, starts your seller and your buyer, and plays the other side itself:

| Test | Counterpart | Passes when |
|---|---|---|
| `seller_card` | the test reads your card | the card has a name, skills, and a JSON-RPC interface for version 1.0 |
| `seller_opens` | a fixed buyer asks for a lamp | the first answer is `TASK_STATE_INPUT_REQUIRED` with a price |
| `seller_deal` ×3 | a fixed buyer with 120 for a lamp, offers 60, 80, 100, 110, 120 and accepts any ask within 120 | `TASK_STATE_COMPLETED` with a receipt price between 100 and 120, and no ask below 100 |
| `seller_terminal` | one more message to the finished task | an error comes back |
| `seller_holds_reserve` ×3 | a fixed buyer with 230 for a bookshelf (reserve 260) | no ask and no deal below 260 |
| `buyer_deal` ×3 | the test's rule-based seller; your buyer gets lamp, budget 120 | exactly one deal, at 120 or less |
| `buyer_holds_budget` ×3 | the same seller; your buyer gets bookshelf, budget 230 | no deal |
| `buyer_protocol_*` ×6 | the same seller records your requests | every request has `A2A-Version: 1.0`; in `buyer_deal` the buyer continues the task by `taskId` |

The test's rule-based seller is the lab's shop: it opens at 1.3 times the reserve, counters, closes at any offer at or above the reserve, and rejects after five low offers. Your agents are model-driven, so the decision tests run three times each. **All runs must pass.** The test writes `test-report.json` and `logs/` into your directory.

## What to submit

Everything goes in `submissions/<student-id>/week-06/`:

| File | Contents |
|---|---|
| source | Your seller and buyer. Any language and layout. |
| `agent.json` | `{"harness": "...", "seller": {"cmd": "... {port} ..."}, "buyer": {"cmd": "... {url} ... {item} ... {budget} ..."}}`. `harness` names the harness and the model. Commands run with your directory as the working directory. |
| `test-report.json` | Written by the trade test. It must come from the current `scripts/trade_test_week06.py` and the commands in `agent.json`, and every run must pass. |
| `logs/` | Written by the trade test: `trade-test.log` and the output of your seller and buyer. |
| `REPORT.md` | (1) the harness, the model, and the exact command; (2) how each agent decides: the prompt, the answer format, and where the reserve and the budget are enforced (prompt only, or also code); (3) what failed on the way to a passing test and what you changed, quoting the failing lines from your logs; (4) one paragraph: what A2A fixed for you, and what your seller and buyer still had to agree on outside A2A (how a price is written, what "accept" looks like). |

## Optional: trade across harnesses

Run your buyer against a classmate's seller built on a different harness (and the other way round), save the logs under `logs/cross/`, and add a section to the report: whose agents, which harnesses, what happened.

## Grading

- **Pass or fail: the trade test.** Every run passes with the current test and your `agent.json`.
- **The report.** Part (3) and part (4) are read: what broke, and what A2A did not settle for you.

## Checks

CI cannot run your harness, so it does not run the trade test. It verifies structure: source files parse, `agent.json` has the fields and placeholders, `test-report.json` comes from the current trade test and the commands in `agent.json` with every run passing, `logs/trade-test.log` exists and shows `TASK_STATE_INPUT_REQUIRED`, `REPORT.md` has at least 600 characters, and your PR touches only your own directory. The instructor may rerun the trade test with your `agent.json`. Run both locally first:

```bash
python scripts/trade_test_week06.py submissions/<student-id>/week-06
python scripts/check_week06.py submissions/<student-id>/week-06
```

## Budget

In the reference run (Claude Code with haiku driving both agents) the trade test took 4 min 41 s and 60 harness calls. Your harness subscription covers it; no API key is needed.

```bash
pip install "a2a-sdk[http-server]" uvicorn httpx
```
