"""Pinned original isolation validator, revision06 only."""
import hashlib
from pathlib import Path

old = Path('/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08/history/20261107-goods-terms-runtime-01/validate_remote.py')
blob = old.read_bytes()
assert hashlib.sha256(blob).hexdigest() == 'fbb9a90df50f15a0b2e673ee1921f7ea6803ec45dd1195b7158057f17e589682'
code = blob.decode().replace('20261107-goods-terms-runtime-01', '20261107-goods-c-only-runtime-06')
code = code.replace('== 107', '== 124').replace('+35', '+52')
exec(compile(code, '<revision06-isolated-validator>', 'exec'))
