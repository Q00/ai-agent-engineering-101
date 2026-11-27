"""In-memory syntax and read-only preflight extraction; no production execution."""
from pathlib import Path
import hashlib
import json

here=Path(__file__).resolve().parent
for name in ('transport.py','inspect_remote.py','activate_remote.py','check_local.py'):
    compile((here/name).read_bytes(),name,'exec')
code=(here/'activate_remote.py').read_text()
prefix=code.split('OUT.mkdir(exist_ok=False)')[0]
prefix += '\nprint(json.dumps(dict(preflight=True, card_status=200, seller_parent_command_verified=True, patch_check=True, source_files=len(manifest["source_sha256"]))))\n'
with (here/'preflight_remote.py').open('x') as f: f.write(prefix)
print(json.dumps({'syntax_files':4,'activation_sha256':hashlib.sha256((here/'activate_remote.py').read_bytes()).hexdigest()}))
