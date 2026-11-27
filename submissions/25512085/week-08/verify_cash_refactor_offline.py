"""Run reviewed regressions with repository data and external I/O guarded.

Usage: python -B verify_cash_refactor_offline.py baseline|after
Only a new, exclusive log and unique temporary fixtures may be written.
OPENCODE_TEMP_DIR overrides the temporary parent (must already exist). Otherwise
prefer the approved per-user opencode folder, then the platform temp directory.
"""
import contextlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime


BASE = Path(__file__).resolve().parent
PREFERRED_TEMP = Path.home() / 'AppData/Local/Temp/opencode'
MODULES = (
    'test_seller_cash_offline', 'test_sku_break_rates_offline',
    'test_grade_warranty_offline', 'test_goods_floor_offline',
    'test_goods_selection_offline', 'test_warranty_disclosure_offline',
    'test_hub_compaction_offline', 'test_unified_buying_offline',
    'test_verify_cash_refactor_offline',
    'test_seller_forum_offline',
    'test_procurement_guard_offline',
    'test_market_research_offline',
    'test_endgame_taper_offline', 'test_restock_equity_target_offline',
    'test_buyer_price_offline', 'test_buyer_retry_offline',
    'test_buyer_failure_reason_offline', 'test_buyer_stockout_cooldown_offline',
    'test_buyer_gap_counter_offline',
    'test_seller_reputation_offline',
)


def select_temp_parent():
    override = os.environ.get('OPENCODE_TEMP_DIR')
    parent = (Path(override).expanduser() if override else
              PREFERRED_TEMP if PREFERRED_TEMP.is_dir() else Path(tempfile.gettempdir()))
    parent = parent.resolve()
    if not parent.is_dir() or parent.is_relative_to(BASE.parents[2]):
        raise ValueError('temporary parent must exist outside the repository')
    return parent


def main():
    phase = sys.argv[1]
    if phase not in ('baseline', 'after'):
        raise ValueError('expected baseline or after')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    log = BASE / 'logs' / f'cash-refactor-{phase}-{stamp}.log'
    temp_parent = select_temp_parent()
    with log.open('x', encoding='utf-8') as output, tempfile.TemporaryDirectory(
            prefix='cash-refactor-', dir=temp_parent) as tmp:
        root = Path(tmp).resolve()
        tempfile.tempdir = str(root)

        def guard(event, args):
            if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen'):
                raise AssertionError(f'external I/O forbidden: {event}')
            if event == 'open' and isinstance(args[0], (str, bytes)):
                path = Path(args[0]).resolve()
                mode, flags = args[1:3]
                writing = (mode is not None and any(c in mode for c in 'wax+')) or flags & 0x703
                if path.is_relative_to(BASE):
                    if writing:
                        raise AssertionError(f'repository write forbidden: {path}')
                    if ('.market' in path.parts or path.suffix == '.pem'
                            or path.name in ('state_hub.json', 'seller_history.json')
                            or 'logs' in path.parts or 'history' in path.parts):
                        raise AssertionError(f'original data read forbidden: {path}')
                if writing and not path.is_relative_to(root):
                    # Existing seller fixtures explicitly choose this approved temp parent.
                    if not (path.is_relative_to(temp_parent) or
                            PREFERRED_TEMP.is_dir() and path.is_relative_to(PREFERRED_TEMP.resolve())):
                        raise AssertionError(f'non-temporary write forbidden: {path}')

        sys.addaudithook(guard)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            print(f'Command: python -B verify_cash_refactor_offline.py {phase}')
            print(f'Interpreter: {sys.executable}')
            print(f'Temporary parent: {temp_parent}')
            print('Guards: no external I/O, no repository writes, no original market/state/key/log reads')
            import seller_cash_policy as cash
            api = {name: str(inspect.signature(value)) for name, value in vars(cash).items()
                   if inspect.isfunction(value) and value.__module__ == cash.__name__}
            print('Public API: ' + json.dumps(api, sort_keys=True))
            modules = list(MODULES)
            if (BASE / 'test_cash_policy_modules_offline.py').exists():
                modules.append('test_cash_policy_modules_offline')
            if (BASE / 'test_buyer_savings_ranking_offline.py').exists():
                modules.append('test_buyer_savings_ranking_offline')
            if (BASE / 'test_forum_quality_evidence_offline.py').exists():
                modules.extend(('test_forum_intel_offline', 'test_forum_quality_evidence_offline'))
            suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        print(log)
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
