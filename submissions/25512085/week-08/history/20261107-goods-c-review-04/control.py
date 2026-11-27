"""Revision06 exclusive source-only controller. No activation or restart."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
old = HERE.parent/'20261107-goods-c-review-01/control.py'
code = old.read_text().replace('c-only-runtime-03', 'c-only-runtime-06')
exec(compile(code, str(old), 'exec'))
