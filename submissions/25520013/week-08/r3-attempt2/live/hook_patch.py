"""Insert the attempt-2 switch into attempt 1's hooks/market_buyer.py or hooks/restock.py (stdlib only, idempotent).

    python3 live/hook_patch.py <W>/hooks/market_buyer.py --code ~/r3a2-code [--check]
    python3 live/hook_patch.py <W>/hooks/restock.py --code ~/r3a2-code [--check]

The `if __name__ == "__main__":` block gets a branch read on every run: when <cwd>/a2/LIVE exists (the kit runs
hooks with cwd = W) it runs kernel.buyer_exec / kernel.restock with the kit's own arguments plus --root W and exits
with its code; otherwise attempt 1's main() runs unchanged. If the attempt-2 code cannot be imported, attempt 1
runs. A crash inside attempt-2 restock prints an empty plan, like attempt 1's own safety net. The anchor must occur
exactly once; a file with the marker is left alone ("already patched"); the original is copied to <file>.bak-a2.
"""

import argparse
import os
import shutil
import sys

MARKER = "# r3a2"
ANCHOR = 'if __name__ == "__main__":\n    main()\n'
MODULES = {"market_buyer.py": "buyer_exec", "restock.py": "restock"}

BRANCH = """\
if __name__ == "__main__":
    import os as _os, sys as _sys                 # r3a2 switch: <W>/a2/LIVE, read per run
    if _os.path.exists(_os.path.join(_os.getcwd(), "a2", "LIVE")):
        try:
            _sys.path.insert(0, {code!r})
            from kernel import {module} as _a2
        except Exception:                         # noqa: BLE001 - no attempt-2 code: attempt 1 runs
            _a2 = None
        if _a2 is not None:
{run}
    main()
"""

RUN_BUYER = """\
            raise SystemExit(_a2.main(_sys.argv[1:] + ["--root", _os.getcwd()]))"""

RUN_RESTOCK = """\
            try:
                _rc = _a2.main(_sys.argv[1:] + ["--root", _os.getcwd()])
            except Exception:                     # noqa: BLE001 - empty plan this cycle, like attempt 1
                import traceback as _tb
                _tb.print_exc()
                print('{"buy": []}', flush=True)
                _rc = 0
            raise SystemExit(_rc)"""


def patched(src, code, module):
    """The patched source. Raises AssertionError when the anchor is not found exactly once."""
    n = src.count(ANCHOR)
    assert n == 1, f"anchor found {n} times"
    run = RUN_BUYER if module == "buyer_exec" else RUN_RESTOCK
    return src.replace(ANCHOR, BRANCH.format(code=code, module=module, run=run))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path")
    ap.add_argument("--code", required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    module = MODULES.get(os.path.basename(a.path))
    if module is None:
        print(f"refused: {a.path} is not one of {sorted(MODULES)}")
        return 2
    code = os.path.abspath(os.path.expanduser(a.code))
    with open(a.path, encoding="utf-8") as f:
        src = f.read()
    if MARKER in src:
        print("already patched")
        return 0
    new = patched(src, code, module)
    try:  # this python may be older than the hook's: then the compile check is skipped
        compile(src, a.path, "exec")
    except SyntaxError:
        print("note: original does not compile with this python; compile check skipped")
    else:
        compile(new, a.path, "exec")
    if a.check:
        print(f"would apply: {a.path} -> kernel.{module} (code {code})")
        return 0
    shutil.copy2(a.path, a.path + ".bak-a2")
    with open(a.path, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"patched: {a.path} -> kernel.{module} (backup {a.path}.bak-a2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
