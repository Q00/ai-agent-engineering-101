"""Start the unchanged course kit against the verified current lab shop URL."""
import os
from pathlib import Path
root = Path(__file__).resolve().parent.parent
state = root / '.market'
url = 'https://cheapest-in-town-26520057.cheapest-in-town-worker.workers.dev'
if (state/'gpt-cutover-enabled').exists():
    url = (state/'gpt-public-url').read_text().strip()
    if not url.startswith('https://'):
        raise SystemExit('Invalid lab shop URL')
python = str(root/'runtime/venv/bin/python')
os.chdir(root)
os.execv(python,[python,'-u','runtime/market_kit.py','join','--dir','.',
    '--market','https://market.ourolabs.ai','--name','cheapest-in-town','--deployed',url])
