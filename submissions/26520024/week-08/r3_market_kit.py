"""Opt-in kit launcher with durable checkout gating; importing does not join."""
import importlib.util
import argparse
import os
from pathlib import Path
import sys

from r3_checkout import guarded_shop
from r3_common import ROOT, require_live
from r3_storage import configure_database, exclusive_lock


def load_kit(path=None):
    path = Path(path) if path else ROOT.parents[2] / 'scripts' / 'market_kit.py'
    spec = importlib.util.spec_from_file_location('week08_course_kit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.KIT_VERSION != '4' or not hasattr(module.AcpShop, 'create'):
        raise RuntimeError('Review checkout integration before using a different kit contract')
    return module


def launch_directory(argv):
    if not argv or argv[0] != 'join':
        raise SystemExit('This guarded launcher supports join only')
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--dir', required=True)
    parser.add_argument('--deployed', nargs='?', const=True)
    args, _ = parser.parse_known_args(argv[1:])
    if args.deployed is not None:
        raise SystemExit('An external shop cannot share this local checkout gate')
    directory = Path(args.dir).resolve()
    if directory != ROOT:
        raise SystemExit('Use this launcher only with its own week-08 --dir')
    return directory


def main():
    require_live()
    directory = launch_directory(sys.argv[1:])
    dbdir = configure_database(directory / '.market', os.environ.get('R3_DB_DIR'))
    with exclusive_lock(dbdir / 'market-kit.lock'):
        kit = load_kit()
        kit.AcpShop = guarded_shop(kit.AcpShop)
        os.environ['MARKET_DIR'] = str(directory / '.market')
        os.environ['R3_CHECKOUT_GUARD'] = '1'
        kit.main()


if __name__ == '__main__':
    main()
