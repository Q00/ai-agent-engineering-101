"""Stage image code in persistent storage, then launch the existing supervisor.

State is never seeded from the image. Existing keys, memory, logs and the
claim code stay in /data. Startup requires both imported identity keys.

strategy/ is its own volume (compose.yaml mounts week-08/strategy there), shared
with the strategist. The image's SKILL.md only seeds an empty volume; a file that
is already there is never replaced, so tuned values survive new images.
"""
import os
from pathlib import Path
import shutil
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

SOURCE = Path('/opt/agent')
DATA = Path('/data')
DEPLOY = Path('/opt/deploy')
# strategist/ runs from here too (as-agent), so it imports the same policy, memory and model code.
# Its state is /data/strategist-home (STRATEGIST_HOME), never this code dir.
CODE_DIRS = ('memory', 'tools', 'model', 'dns_support', 'tests', 'strategist', 'strategist/tests')


def prepare():
    DATA.mkdir(parents=True, exist_ok=True)
    for source in SOURCE.glob('*.py'):
        shutil.copyfile(source, DATA / source.name)
    for name in CODE_DIRS:
        (DATA / name).mkdir(exist_ok=True)
        # Every file the image has there: strategist/tests also needs its .md and .json fixtures.
        for source in (SOURCE / name).iterdir():
            if source.is_file():
                shutil.copyfile(source, DATA / name / source.name)
    shutil.copyfile(DEPLOY / 'agent.container.json', DATA / 'agent.json')
    (DATA / 'logs').mkdir(exist_ok=True)
    (DATA / '.market').mkdir(mode=0o700, exist_ok=True)
    stage_strategy()
    os.chdir(DATA)
    sys.path.insert(0, str(DATA))


def stage_strategy():
    target = DATA / 'strategy' / 'SKILL.md'
    if target.is_file():
        return
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(SOURCE / 'strategy' / 'SKILL.md', target)
    print('[container] strategy/SKILL.md was missing; seeded from the image', flush=True)


def require_identity():
    for name in ('shop_key.pem', 'buyer_key.pem'):
        path = DATA / name
        if not path.is_file():
            raise ValueError(f'Missing {name}: put the existing key in the data folder (/data) first.')
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
    # A fixed public address (.market/fixed-address.json: a reserved HTTPS URL and the tunnel command, written by
    # configure_ngrok.py or by hand, credentials only in .market/ngrok.yml) keeps the shop's URL across restarts,
    # so other agents' records of this shop (keyed by URL) survive; without it a Quick Tunnel gets a random URL.
    fixed = DATA / '.market' / 'fixed-address.json'
    tunnel = ['--fixed-config', str(fixed)] if fixed.is_file() else ['--cloudflared', '/usr/local/bin/cloudflared']
    print('[container] starting psyoung82 seller, buyer hooks, market kit and '
          + ('the fixed-address tunnel' if fixed.is_file() else 'Quick Tunnel'), flush=True)
    os.execv(sys.executable, [sys.executable, '-u', str(DATA / 'market_runtime.py'),
             '--name', 'psyoung82', '--market', os.environ.get('MARKET_URL', 'https://market.ourolabs.ai'),
             '--dir', str(DATA), '--kit', str(DATA / 'market_kit.py'),
             '--port', '8900', *tunnel, '--require-owner'])


if __name__ == '__main__':
    main()
