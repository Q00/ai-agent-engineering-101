"""Generate reproducibility metadata without collecting credentials."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from codex_backend import backend_info

root = Path(__file__).resolve().parent
repo = root.parents[2]
paths = sorted(root.glob('*.py')) + sorted(root.glob('*.json')) + [root / 'requirements.lock']
paths += [repo / 'scripts/trade_test_week06.py', repo / 'scripts/check_week06.py']
result = dict(python=sys.version, executable=sys.executable, base_prefix=sys.base_prefix,
              model=backend_info(),
              commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
              files={str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
              packages={d.metadata['Name']: d.version for d in importlib.metadata.distributions()})
print(json.dumps(result, indent=2, sort_keys=True))
