"""Adapt the reviewed isolation validator; never replace or restart production."""
import ast
from pathlib import Path

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
REL = 'submissions/25512085/week-08/'
OLD = REPO/REL/'history/20261107-goods-terms-runtime-01/validate_remote.py'
code = OLD.read_text()
code = code.replace('20261107-goods-terms-runtime-01', '20261107-goods-c-only-runtime-02')
code = code.replace("== 107", "== 111")
code = code.replace("+35", "+39")
# The unchanged validator copies only allowlisted tracked source, applies a two-file
# patch inside exclusive evidence trees, blocks live repository/network/child tests,
# and compares every production hash before and after the four test sets.
compile(code, str(OLD), 'exec')
exec(compile(code, '<C-only-isolated-validator>', 'exec'))
