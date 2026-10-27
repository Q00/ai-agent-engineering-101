"""Save the market claim code locally, with hidden input and no secret output."""
import getpass
import os
from pathlib import Path
import tempfile


def kit_environment(private, inherited=None):
    """Use an ignored local credential through the environment, never argv."""
    environment = dict(os.environ if inherited is None else inherited)
    path = Path(private) / 'market-owner'
    if path.exists():
        owner = path.read_text().strip()
        if not owner or any(c.isspace() or c == '\x00' for c in owner):
            raise ValueError('Invalid local market owner configuration; credential not printed')
        environment['MARKET_OWNER'] = owner
    return environment


def save_owner(private, owner):
    owner = owner.strip()
    if not owner or any(c.isspace() or c == '\x00' for c in owner):
        raise ValueError('Enter a nonempty claim code without internal whitespace')
    private = Path(private)
    private.mkdir(exist_ok=True, parents=True)
    fd, temporary = tempfile.mkstemp(prefix='market-owner-', dir=private)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(owner + '\n')
        os.replace(temporary, private / 'market-owner')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    print('Sign in with GitHub at https://market.ourolabs.ai/join and copy your claim code.')
    print('Paste it below. Input is hidden and is saved only in ignored .market/ with private permissions.')
    owner = getpass.getpass('Market claim code (hidden): ')
    try:
        save_owner(Path(__file__).resolve().parent / '.market', owner)
    except (OSError, ValueError):
        raise SystemExit('Credential not saved; existing configuration was retained.')
    print('Local credential saved. The waiting runtime will reconnect automatically.')


if __name__ == '__main__':
    main()
