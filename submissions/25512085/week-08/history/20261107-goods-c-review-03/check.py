"""Final revision05 snapshots without overwriting earlier evidence."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
old = HERE.parent/'20261107-goods-c-review-02/check.py'
exec(compile(old.read_text(), str(old), 'exec'))
