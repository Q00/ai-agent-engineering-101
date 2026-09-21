# Week 03 — Contract Net with LLM contractors

A voice-assistant skill router as a contract net: one manager announces each
user utterance, three LLM contractors (`weather` / `music` / `home`) bid, the
manager awards. `gold` in `tasks.json` is the skill that should win; the
manager never sees it, only the scorer does.

## 1. Setup

- **Provider / model:** OpenAI, `gpt-4o-mini`, `temperature=0` (fixed for every
  call so a change between conditions can only come from the system prompt).
- **No tools.** A bid is one system prompt + one user message (the announcement
  `Task <id>: <desc>`, `gold` withheld). Model glue is trimmed from the week-02
  starter `tools_shared.py` (`llm.py`).
- **Bid contract:** the contractor returns one JSON object
  `{"participate": bool, "confidence": 0..1, "reason": str}`. An unparseable or
  failed reply is recorded as **no bid** (FIPA `not-understood`).
- **Award rule:** among contractors with `participate=true`, highest
  `confidence` wins; ties break by contractor name ascending; if nobody
  participates the task is **unassigned**.
- **Message count (per task):** one announcement per contractor (3) + one per
  returned bid + one award if the task was awarded.
- **Conditions (only the contractor system prompt changes):**
  - `baseline` — three specialists (weather / music / home).
  - `homogeneous` — all three given the same generalist prompt.
  - `overconfident` — baseline, but one contractor (`music`) is told to bid on
    every task with high confidence.
- **How to run:**
  ```bash
  cd submissions/25512081/week-03
  export OPENAI_API_KEY=...           # or OpenRouter: OPENAI_BASE_URL + AGENT_MODEL
  python run.py --runs 3              # writes results.csv and logs/
  python run.py --runs 3 --dry        # offline plumbing check, no key
  ```

## 2. Measurements

Nine runs, three per condition. With `temperature=0` the three runs of each
condition were identical (variance 0), so they are summarised per condition
below; the full per-run rows are in `results.csv`.

| condition | runs | correct / 6 | misawards | unassigned | messages | note |
|---|---|---|---|---|---|---|
| baseline | 3 | 6 | 0 | 0 | 42 | ok |
| homogeneous | 3 | 2 | 4 | 0 | 42 | ok |
| overconfident | 3 | 6 | 0 | 0 | 42 | ok |

`gpt-4o-mini` returned a parseable bid on every call (no `unassigned`, no
`no-bid`), so `messages` is a constant 42 = 6 tasks × (3 announcements + 3 bids
+ 1 award). (An earlier free model, `nvidia/nemotron-3.5-lightning:free`,
intermittently returned empty responses that were recorded as no-bids — see the
commit history; the harness now treats those as findings, not crashes.)

## 3. Smith 1980 vs FIPA vs this reproduction

| Dimension | Smith 1980 (distributed sensing) | FIPA Contract Net (fipa00029) | This reproduction |
|---|---|---|---|
| Nodes | Sensor/processor nodes in one cooperative network | Initiator + Participant agents | 1 manager (Python) + 3 LLM contractors |
| How a bid is produced | Fixed task-evaluation rule per node | `propose`/`refuse` act with preconditions | LLM judges `participate`+`confidence` from its system prompt |
| What guarantees bid honesty | Nodes are parts of one system — no incentive to lie | Nothing in the protocol; agents may be self-interested | Nothing enforced; the `overconfident` prompt tests the gap |
| Allocation quality | Task goes to the best-suited node | Initiator's choice among proposals | `awarded == gold` (correct) vs `misaward` |
| Negotiation cost | Announcement/bid/award messages on the net | `cfp`/`propose`/`accept`/`reject` (+ deadline) | `messages` count (constant 42 here) |
| Failure modes | No bids (idle), free-for-all | Refusals, no proposals, `not-understood` | Tie-collapse (homogeneous); overconfidence gated by `participate`; unparseable reply → no-bid |

## 4. Interpretation

The variable that moved was **allocation quality, not negotiation cost**:
`messages` stayed at 42 in every condition, while `correct` swung from 6 to 2.
**`baseline`** routed perfectly (6/6): each specialist bid `participate=true`
only on its own tasks (weather conf ≈ 0.90–1.0) and refused the rest, so the
award was never contested. **`homogeneous`** collapsed to 2/6: with identical
generalist prompts all three returned `participate=true, confidence=0.90` on
every task (`bid[weather] … I can provide weather information` — and `music`
and `home` say the same), leaving the award to the tie-break, which is name
order, so `home` swept and the four non-home tasks became misawards. The
surprise is **`overconfident`**, which did **not** sweep (6/6): telling `music`
to bid high on everything raised its *confidence* (it reports 0.90 even on
weather) but the model still set `participate=false` on out-of-area tasks
(`bid[music] participate=False conf=0.90 :: this task is about weather, which is
outside my area`). Because the award rule only ranks *participants*, the
inflated confidence never entered the contest. The lesson is that in this
protocol the honesty that matters is the **participate flag, not the confidence
number** — Smith's protocol has no defense against a lying bid, but here the
lie landed on the one field the award ignored. To actually reproduce the
"overconfident contractor sweeps the board" failure, the prompt would have to
force `participate=true` on every task, or the award rule would have to rank on
confidence regardless of participation.
