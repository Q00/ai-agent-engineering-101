# Week 06: A2A Buyer and Seller

Student ID: 26512073  
Date: 2026-10-17

## 1. Setup

I connected a buyer and a seller through A2A v1.0 with JSON-RPC.
Both use Codex CLI. The saved runs used `gpt-6.1-sol`.
The code uses the configured default model, so changing the Codex settings
may change the model. The exact harness command is:

```text
codex exec --skip-git-repo-check --ephemeral --sandbox read-only --color never -c project_doc_max_bytes=0 -
```

The prompt is sent through standard input. Each call starts a new conversation
in an empty working directory. Project document loading is disabled.
The harness returns JSON. Python reads it and sends the A2A response.
Package versions are in `requirements.txt`. Launch commands are in `agent.json`.
The Codex CLI needs an existing login.

From the repository root, I ran:

```powershell
& submissions/26512073/week-06/.venv/Scripts/python.exe submissions/26512073/week-06/development_support/trade_test_week06.py submissions/26512073/week-06
```

This used the saved instructor test snapshot. It still needs to be compared
with the current official test before submission.

## 2. Agent decisions

The seller sells a lamp, chair, and bookshelf. Their reserves are 100, 180,
and 260. Its prompt asks for an opening price around 30 percent above reserve.
It accepts an offer at or above reserve. After the first low offer, it counters
at reserve. If the buyer offers less again, it rejects. I chose this strategy
during our discussion. The exact prompts are in `seller_decision.py`.
The JSON answer has `action`, `price`, and `reason`.

The buyer prompt asks it to accept an affordable asking price, or increase
its offers within budget. It should reach its budget by the fourth offer
if needed. If that offer is refused, it walks away. The exact prompt is in
`buyer.py`. Its JSON answer also has `action`, `price`, and `reason`.

The harness chooses every price and trading action. Both prompts include
the private limit. Python also blocks seller prices below reserve and buyer
offers above budget. A seller acceptance must use the buyer's actual offer.

## 3. Failures, fixes, and results

The first CLI trial failed with `Could not find home directory` in the
sandboxed environment. Running with access to the existing login worked.
The first full trade test then failed with `codex is not on PATH`.
I added CLI discovery for the VS Code installation and an optional
`CODEX_EXECUTABLE` setting.

The next full test passed 14 of 21 checks. The harness logs showed
`ERROR: exceeded retry limit, last status: 429 Too Many Requests`.
I added up to four attempts with waits of 5, 10, and 20 seconds.
All attempts share the original timeout. Each attempt has its own log.
Five offline checks tested retries and error handling with simulated outputs.
These checks are separate from real trading results.

The final trade log says `21/21 passed -> PASS`.
The seller sold each lamp at 100. The buyer bought each lamp at 100,
within its budget of 120. For bookshelves, the buyer reached its budget
of 230 and canceled without a deal. All protocol checks passed.
Earlier failures remain in commits and archived logs.

## 4. What A2A did and did not decide

A2A gave us an Agent Card, task IDs, task states, cancellation, and receipt
artifacts. The buyer could continue the same task and see when it ended.
We still had to agree that an item name starts shopping, a number means
an offer, and the last number in a seller message is its asking price.
Acceptance uses a numeric offer, and a receipt stores a numeric `price`.
A2A did not choose a fair price or enforce our private limits. Our prompts
and Python checks handled those choices and limits.

I used AI assistance for coding, debugging, testing, and simple English wording.
