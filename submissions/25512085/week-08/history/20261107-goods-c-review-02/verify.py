"""Reuse parser-only AST/source/evidence verification with unique syntax outputs."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
old = HERE.parent/'20261107-goods-c-review-01/verify.py'
code = old.read_text().replace('for path in paths:', 'for index, path in enumerate(paths):')
code = code.replace("(path.name+'c')", "(str(index)+'-'+path.name+'c')")
exec(compile(code, str(old), 'exec'))
