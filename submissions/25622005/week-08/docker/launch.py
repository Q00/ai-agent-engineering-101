"""Stage image code in persistent storage, then launch the existing supervisor.

State is never seeded from the image. Existing keys, memory, logs and the
claim code stay in /data. Startup requires both imported identity keys.

strategy/SKILL.md is replaced only when the image brings a different version,
so a live edit in /data survives restarts until the next image is deployed;
the replaced file is kept beside it.
"""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

SOURCE = Path('/opt/agent')
DATA = Path('/data')
DEPLOY = Path('/opt/deploy')
CODE_DIRS = ('memory', 'tools', 'model', 'dns_support', 'tests')


def prepare():
    DATA.mkdir(parents=True, exist_ok=True)
    for source in SOURCE.glob('*.py'):
        shutil.copyfile(source, DATA / source.name)
    for name in CODE_DIRS:
        (DATA / name).mkdir(exist_ok=True)
        for source in (SOURCE / name).glob('*.py'):
            shutil.copyfile(source, DATA / name / source.name)
    shutil.copyfile(DEPLOY / 'agent.container.json', DATA / 'agent.json')
    (DATA / 'logs').mkdir(exist_ok=True)
    (DATA / '.market').mkdir(mode=0o700, exist_ok=True)
    stage_strategy()
    os.chdir(DATA)
    sys.path.insert(0, str(DATA))


def stage_strategy():
    image = (SOURCE / 'strategy' / 'SKILL.md').read_bytes()
    target = DATA / 'strategy' / 'SKILL.md'
    stamp = DATA / '.market' / 'skill.image.sha256'
    digest = hashlib.sha256(image).hexdigest()
    if target.is_file() and stamp.is_file() and stamp.read_text().strip() == digest:
        return
    target.parent.mkdir(exist_ok=True)
    if target.is_file() and target.read_bytes() != image:
        backup = target.with_name(f'SKILL.md.{time.strftime("%Y%m%d-%H%M%S")}.bak')
        os.replace(target, backup)
        print(f'[container] strategy replaced by the new image; previous file kept as strategy/{backup.name}',
              flush=True)
    target.write_bytes(image)
    stamp.write_text(digest + '\n')


def require_identity():
    for name in ('shop_key.pem', 'buyer_key.pem'):
        path = DATA / name
        if not path.is_file():
            raise ValueError(f'Missing {name}: import the existing Mac key into .market/n36l-data first.')
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


def report_policy():
    """An invalid SKILL.md does not stop the shop: policy.py falls back to the last good one."""
    import policy
    _, ver, errors = policy.load()
    print(f'[container] policy {ver}', flush=True)
    for error in errors:
        print(f'[container] policy error: {error}', flush=True)


def main():
    prepare()
    command = sys.argv[1:] or ['run']
    if command != ['run']:
        os.execvp(command[0], command)
    try:
        require_identity()
    except ValueError as error:
        raise SystemExit(str(error))
    report_policy()
    print('[container] starting psyoung82 seller, buyer hooks, market kit and Quick Tunnel', flush=True)
    os.execv(sys.executable, [sys.executable, '-u', str(DATA / 'market_runtime.py'),
             '--name', 'psyoung82', '--market', os.environ.get('MARKET_URL', 'https://market.ourolabs.ai'),
             '--dir', str(DATA), '--kit', str(DATA / 'market_kit.py'),
             '--port', '8900', '--cloudflared', '/usr/local/bin/cloudflared', '--require-owner'])


if __name__ == '__main__':
    main()
