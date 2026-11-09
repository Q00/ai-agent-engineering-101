"""Load a local credential without printing it, then run the assignment."""
import os
from pathlib import Path
import runpy

from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
env_file = HERE / '.env'
if not env_file.exists():
    env_file = HERE.parents[2] / '.env'
if env_file.exists():
    raw = env_file.read_text().strip()
    if len(raw.splitlines()) == 1 and '=' not in raw:
        os.environ.setdefault('OPENAI_API_KEY', raw)
    else:
        for name, value in dotenv_values(env_file).items():
            if value and name in {'OPENAI_API_KEY', 'OPENROUTER_API_KEY',
                                  'AGENT_MODEL'}:
                os.environ.setdefault(name, value)
if not os.environ.get('OPENAI_API_KEY'):
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        raise SystemExit('Set OPENROUTER_API_KEY or OPENAI_API_KEY locally.')
    os.environ['OPENAI_API_KEY'] = key
os.environ['OPENAI_BASE_URL'] = 'https://openrouter.ai/api/v1'
os.environ.setdefault('AGENT_MODEL', 'nvidia/nemotron-3.5-lightning:free')
os.chdir(HERE)
print('Provider: OpenRouter; model:', os.environ['AGENT_MODEL'], flush=True)
runpy.run_path(str(HERE / 'first_agent.py'), run_name='__main__')
