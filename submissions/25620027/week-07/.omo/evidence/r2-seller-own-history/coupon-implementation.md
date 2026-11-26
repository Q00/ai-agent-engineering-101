# R2WELCOME coupon implementation
UTC: 2026-10-20T12:18:30.671178+00:00

Changed seller_policy.py and seller_turn.py only. First downward quote is guarded to max(reserve, previous ask minus 5); typed coupon_used persists across later quotes. Actual discount appears only when applied, with coupon metadata and final numeric ask. Subsequent quotes retain monotonic guard; receipt/accept prices unchanged. Own numeric price history preserved.

Syntax command: PYTHONPYCACHEPREFIX=/tmp/ax-w07-r2-pycache .venv/bin/python -m py_compile market_a2a/seller_policy.py market_a2a/seller_turn.py
Observed: exit 0, empty stdout/stderr. Behavioral tests and API verification skipped on explicit time-critical instruction. Parent owns deployment; no runtime success claimed here.

Source hashes:
seller_policy.py: d8e9e80de138580c908803e04353bb7f32e974536c594307b7fe116336a65049
seller_turn.py: 0fb7cc705e71e8670d39a12ac4090451612469eea9975a1e87ab0d6ab86c6cc3
