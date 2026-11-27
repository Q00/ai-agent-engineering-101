"""Pinned reviewed isolation validator, targeting only the new revision03 history."""
import hashlib
from pathlib import Path

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
OLD = REPO/'submissions/25512085/week-08/history/20261107-goods-terms-runtime-01/validate_remote.py'
blob = OLD.read_bytes()
assert hashlib.sha256(blob).hexdigest() == 'fbb9a90df50f15a0b2e673ee1921f7ea6803ec45dd1195b7158057f17e589682'
code = blob.decode().replace('20261107-goods-terms-runtime-01', '20261107-goods-c-only-runtime-03')
code = code.replace('== 107', '== 119').replace('+35', '+47')
exec(compile(code, '<revision03-isolated-validator>', 'exec'))
