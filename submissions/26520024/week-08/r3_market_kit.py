"""Opt-in kit launcher with durable checkout gating; importing does not join."""
import importlib.util
import os
from pathlib import Path
import sys

from r3_checkout import guarded_shop
from r3_common import ROOT, require_live


def load_kit(path=None):
    path = Path(path) if path else ROOT.parents[2] / 'scripts' / 'market_kit.py'
    spec = importlib.util.spec_from_file_location('week08_course_kit', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.KIT_VERSION != '4' or not hasattr(module.AcpShop, 'create'):
        raise RuntimeError('Review checkout integration before using a different kit contract')
    return module


def main():
    require_live()
    if len(sys.argv) < 2 or sys.argv[1] != 'join':
        raise SystemExit('This guarded launcher supports join only')
    if '--deployed' in sys.argv:
        raise SystemExit('An external shop cannot share this local checkout gate')
    kit = load_kit()
    kit.AcpShop = guarded_shop(kit.AcpShop)
    os.environ['R3_CHECKOUT_GUARD'] = '1'
    kit.main()


if __name__ == '__main__':
    main()
