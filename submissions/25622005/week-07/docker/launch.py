"""Stage image code in persistent storage, then launch the existing supervisor.

State is never seeded from the image. Existing keys, memories, logs and the
claim code stay in /data. Startup requires both imported identity keys.
"""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

SOURCE = Path('/opt/agent')
DATA = Path('/data')
DEPLOY = Path('/opt/deploy')


def prepare():
    DATA.mkdir(parents=True, exist_ok=True)
    for source in SOURCE.glob('*.py'):
        shutil.copyfile(source, DATA / source.name)
    shutil.copytree(SOURCE / 'dns_support', DATA / 'dns_support', dirs_exist_ok=True)
    shutil.copytree(SOURCE / 'tests', DATA / 'tests', dirs_exist_ok=True)
    shutil.copyfile(DEPLOY / 'agent.container.json', DATA / 'agent.json')
    (DATA / 'logs').mkdir(exist_ok=True)
    (DATA / '.market').mkdir(mode=0o700, exist_ok=True)
    os.chdir(DATA)
    sys.path.insert(0, str(DATA))


def require_identity():
    for name in ('shop_key.pem', 'buyer_key.pem'):
        path = DATA / name
        if not path.is_file():
            raise ValueError(f'Missing {name}: import the existing Mac key into .market/nas-data first.')
        try:
            key = load_pem_private_key(path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError):
            raise ValueError(f'Cannot load {name}; key contents are not printed.') from None
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f'{name} must be an Ed25519 key.')
        path.chmod(0o600)
    from configure_market_owner import kit_environment
    if not kit_environment(DATA / '.market').get('MARKET_OWNER'):
        raise ValueError('Missing market claim code. Run: docker compose run --rm market python configure_market_owner.py')
    # Check locally stored login without starting a model request or revealing it.
    try:
        result = subprocess.run(['claude', 'auth', 'status', '--json'],
                                capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Cannot check Claude Code login. Run: docker compose run --rm -it market claude (use the interactive login screen)') from None
    if result.returncode in (-signal.SIGILL, 128 + signal.SIGILL):
        raise ValueError('Claude Code crashed with Illegal instruction (SIGILL). Rebuild the Node.js compatibility image; this is a CPU compatibility failure, not a login failure.')
    if result.returncode:
        raise ValueError('Claude Code is not logged in. Run: docker compose run --rm -it market claude (use the interactive login screen)')


def main():
    prepare()
    command = sys.argv[1:] or ['run']
    if command != ['run']:
        os.execvp(command[0], command)
    try:
        require_identity()
    except ValueError as error:
        raise SystemExit(str(error))
    print('[container] starting psyoung82 seller, buyer, market kit and Quick Tunnel', flush=True)
    os.execv(sys.executable, [sys.executable, '-u', str(DATA / 'market_runtime.py'),
             '--name', 'psyoung82', '--market', os.environ.get('MARKET_URL', 'https://market.ourolabs.ai'),
             '--dir', str(DATA), '--kit', '/opt/market/market_kit.py',
             '--port', '8900', '--cloudflared', '/usr/local/bin/cloudflared', '--require-owner'])


if __name__ == '__main__':
    main()
