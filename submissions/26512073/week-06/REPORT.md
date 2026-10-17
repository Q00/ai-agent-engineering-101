# Week 06: A2A Buyer and Seller

Student ID: 26512073

## 1. Setup

I built an A2A v1.0 buyer and seller using Codex CLI.
The recorded model was gpt-6.1-sol; the code uses the configured default.

Harness command:
```text
codex exec --skip-git-repo-check --ephemeral --sandbox read-only --color never -c project_doc_max_bytes=0 -
```

Launch commands are in agent.json. Package versions are in requirements.txt.

## 2. Decisions

The seller opens around 30% above reserve. It counters a low offer
at reserve, then rejects another low offer.
The buyer raises offers within budget and walks away when needed.

Prompts are in seller_decision.py and buyer.py.
The harness returns JSON with action, price, and reason.
Both prompts include private limits. Python also checks these limits.

## 3. Failures and results

Early logs showed "Could not find home directory" and
"codex is not on PATH". Access to the login and CLI discovery fixed these.

The next test passed 14/21. Logs showed
"429 Too Many Requests". I added bounded retries with increasing waits.
Five offline retry checks passed.

The final test passed 21/21. Lamp deals closed at 100.
The buyer canceled bookshelf negotiations within its budget of 230.
Failed attempts remain in commits and logs.
The saved test still needs comparison with the current official version.

## 4. What A2A handled

A2A provided discovery, task IDs, task states, cancellation, and artifacts.
We still had to agree that numbers mean offers, the last number means
the asking price, and receipts contain a numeric price.
Our prompts and code handled trading decisions and private limits.

I used AI help for coding, debugging, testing, and English wording.
