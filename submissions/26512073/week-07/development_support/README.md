# Development checks

Run from the week-07 folder:

```powershell
& ./.venv/Scripts/python.exe -m unittest test_agent -v
& ./.venv/Scripts/python.exe development_support/check_round1_buyer.py
& ./.venv/Scripts/python.exe development_support/check_live_pricing_decisions.py
```

The unittest suite uses isolated simulated data. The two check scripts make real model calls and save their own evidence. They were moved without running live market experiments during this refactor.

Seller decisions, history readers, and pricing state now live in `seller_strategy.py`. The persisted data remains `pricing_state.json`; keys and logs stay in their original locations. Existing historical logs retain their original commands and filenames.
