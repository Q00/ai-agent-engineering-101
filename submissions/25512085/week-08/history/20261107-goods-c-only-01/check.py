"""Reuse the reviewed source-only snapshot harness in a new exclusive history."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / '20261107-goods-terms-01'
runner = HERE / 'run_isolated.py'
if not runner.exists():
    with runner.open('xb') as stream:
        stream.write((OLD / 'run_isolated.py').read_bytes())
exec(compile((OLD / 'check.py').read_text(encoding='utf-8'), str(OLD / 'check.py'), 'exec'))
