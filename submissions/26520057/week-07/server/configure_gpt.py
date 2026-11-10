"""Run on the lab server from an interactive SSH terminal. Never paste keys into chat."""
import getpass
import json
import os
from pathlib import Path
import urllib.request
import urllib.error

root = Path(__file__).resolve().parent.parent
key = getpass.getpass('OpenAI API key (hidden input): ').strip()
if not key or any(c.isspace() for c in key):
    raise SystemExit('Invalid empty key or whitespace; no changes made.')
request = urllib.request.Request('https://api.openai.com/v1/models/gpt-5.4-mini',
                                 headers={'Authorization':'Bearer '+key})
try:
    with urllib.request.urlopen(request, timeout=20) as response:
        json.load(response)
except urllib.error.HTTPError as e:
    raise SystemExit(f'API key/model check failed: HTTP {e.code}; no changes made.') from None
except Exception:
    raise SystemExit('Could not reach the API; no changes made.') from None
path = root / '.market' / 'gpt.env'
path.parent.mkdir(mode=0o700,exist_ok=True)
if path.exists():
    raise SystemExit('gpt.env already exists; existing configuration preserved.')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd,'w') as f:
    f.write('OPENAI_API_KEY='+key+'\nOPENAI_MODEL=gpt-5.4-mini\nSHOP_PORT=8900\n')
print('API key registered privately. Tell Codex it is ready; public shop has not been switched yet.')
