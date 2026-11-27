"""New exclusive review snapshots using the reviewed no-network test harness."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
runner = HERE/'run_isolated.py'
if not runner.exists():
    with runner.open('xb') as stream:
        stream.write((HERE.parent/'20261107-goods-c-only-01/run_isolated.py').read_bytes())
old = HERE.parent/'20261107-goods-terms-01/check.py'
exec(compile(old.read_text(encoding='utf-8'), str(old), 'exec'))
