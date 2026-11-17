# Week 06: A2A buyer and seller

Run from this directory with a signed-in Codex CLI. No API key is read from `.env`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python ../../../scripts/trade_test_week06.py .
.venv/bin/python challenge1.py
.venv/bin/python challenge2.py
python3 ../../../scripts/check_week06.py .
```

`agent.json` supplies the seller and buyer commands for the official trade test. The
default seller advertises lamp, chair, and bookshelf. `seller.py --catalog catalog.json`
accepts product descriptions against a 2,346-item catalog. The `--profile` option
runs a shop with a different name and reserve table. `challenge2.py`
starts twenty distinct shop endpoints and runs `multi_buyer.py` against their Agent
Cards. Each challenge run writes a new `attempt-NN` directory so earlier logs remain.

The harness is `gpt-5.6-luna` through `codex exec`. Override only for a new run with
`WEEK06_MODEL`; update `agent.json` before submitting results made with another model.
Codex CLI does not expose temperature here, so it is not set.
