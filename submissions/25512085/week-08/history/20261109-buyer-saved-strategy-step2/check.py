"""Reuse step1 isolation, with step2 outputs and explicitly added new fixture."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / '20261109-buyer-saved-strategy-step1/check.py'
# Run the reviewed runner with this step's __file__, preserving exclusive logs,
# source-only copies and network/runtime-data guards. No old output is touched.
code = SOURCE.read_text(encoding='utf-8').replace(
    "'test_buyer_outcomes_saved_offline.py'))",
    "'test_buyer_outcomes_saved_offline.py', 'test_buyer_saved_risk_offline.py'))")
# Labels belong in log names, not source-tree or runtime temp paths: a long
# label made reservation .tmp names exceed Windows MAX_PATH (262 characters).
# The UUID suffix still gives every run exclusive, in-scope scratch/home dirs.
code = code.replace("tree = OUT / 'evidence' / unique",
                    "tree = OUT / 'evidence' / (('diagnose-' if label.startswith('diagnose-') else 'r-') + unique.rsplit('-', 1)[-1])")
code = code.replace("scratch = tree.parent / ('scratch-' + tree.name)",
                    "scratch = tree.parent / ('s-' + tree.name.rsplit('-', 1)[-1])")
code = code.replace("home = tree.parent / ('home-' + tree.name)",
                    "home = tree.parent / ('h-' + tree.name.rsplit('-', 1)[-1])")
code = code.replace("with log.open('x', encoding='utf-8') as output:",
                    "with log.open('x', encoding='utf-8') as output:\n"
                    "            print('Command: ' + repr(sys.argv), file=output, flush=True)\n"
                    "            print('Evidence: ' + str(tree.relative_to(REPO)), file=output, flush=True)")
code = code.replace("    runpy.run_module('unittest', run_name='__main__')", """
    if tree.name.startswith('diagnose-'):
        import threading
        from unittest.mock import _patch
        supply_errors = {}
        def trace_supply(frame, event, result):
            target = (frame.f_code.co_name == 'supply_allowed'
                      and frame.f_code.co_filename.endswith('guarded_market_kit.py'))
            if not target:
                return None
            if event == 'exception':
                supply_errors[id(frame)] = {'type': result[0].__name__, 'message': str(result[1]),
                                            'line': frame.f_lineno}
            if event == 'return' and result is False:
                local = frame.f_locals
                # Closure paths and values are exclusively synthetic runner fixtures.
                directory = local.get('md', local.get('directory'))
                info = {'line': frame.f_lineno, 'now': local.get('now'),
                        'opens': local.get('opens'), 'closes': local.get('closes'),
                        'active_patchers': len(_patch._active_patches),
                        'clock_type': type(frame.f_globals['time'].time).__name__,
                        'exception': supply_errors.pop(id(frame), None),
                        'threads': [t.name for t in threading.enumerate()]}
                if directory:
                    invpath = Path(directory) / 'inventory.json'
                    if invpath.exists():
                        info['mtime'] = invpath.stat().st_mtime
                        info['inventory'] = json.loads(invpath.read_text(encoding='utf-8'))
                print('SUPPLY_DIAG ' + json.dumps(info, sort_keys=True))
            return trace_supply

        sys.settrace(trace_supply)
    runpy.run_module('unittest', run_name='__main__')""")
exec(compile(code, str(HERE / 'check.py'), 'exec'),
     {'__name__': '__main__', '__file__': str(HERE / 'check.py')})
