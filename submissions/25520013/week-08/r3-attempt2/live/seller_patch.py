"""Insert the attempt-2 switch into attempt 1's hooks/seller.py (stdlib only, idempotent).

    python3 live/seller_patch.py <W>/hooks/seller.py --code ~/r3a2-code [--check]

Three exact-string insertions, each anchor must occur exactly once: the bridge import after the a2a imports, the
delegation at the top of Seller._execute (after the HOLD_S expiry, before attempt 1 reads its own deal), and the
release of an attempt-2 deal in Seller.cancel. A file that already carries the marker is left alone ("already
patched"). The original is copied to <file>.bak-a2 before writing. --check only reports whether it would apply.
The seller process reads the patch at its next (re)start; the switch itself is <W>/a2/LIVE, read per request.
"""

import argparse
import os
import shutil
import sys

MARKER = "# r3a2"

IMPORT_ANCHOR = (
    "from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill\n"
)
IMPORT_ADD = """
try:                                              # r3a2: attempt-2 seller bridge, on while <W>/a2/LIVE exists
    sys.path.append({code!r})
    from kernel import seller_bridge as A2
except Exception:                                 # noqa: BLE001
    A2 = None
"""

EXEC_ANCHOR = "        deal = self.open.get(task.id)\n        if deal is None:\n"
EXEC_ADD = """\
        if A2 is not None:                        # r3a2 switch: <W>/a2/LIVE, read per request
            _a2 = A2.bridge(os.getcwd(), MARKET_DIR, sign=sign, name=NAME, receipt_terms=P.receipt_terms,
                            offer_in=offer_in, opening_text=opening_text, metric=self._metric, ledger=Ledger,
                            policy=self.refresh_policy, shelf=self.shelf, data_part=new_data_part,
                            a1_held=lambda: {d["unit"]["unit"] for d in self.open.values()})
            if _a2.wants(task.id, task.id in self.open) and await _a2.handle(task.id, text, up, say):
                return
"""

CANCEL_ANCHOR = "        _had = self.open.pop(task.id, None)\n"
CANCEL_ADD = """\
        if A2 is not None:                        # r3a2: free the unit an attempt-2 deal held
            A2.cancel(task.id)
"""


def patched(src, code):
    """The patched source. Raises AssertionError when an anchor is not found exactly once."""
    out = src
    for anchor, add, after in (
        (IMPORT_ANCHOR, IMPORT_ADD.format(code=code), True),
        (EXEC_ANCHOR, EXEC_ADD, False),
        (CANCEL_ANCHOR, CANCEL_ADD, False),
    ):
        n = out.count(anchor)
        assert n == 1, f"anchor found {n} times: {anchor.strip()[:60]!r}"
        out = out.replace(anchor, anchor + add if after else add + anchor)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path")
    ap.add_argument("--code", required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    code = os.path.abspath(os.path.expanduser(a.code))
    with open(a.path, encoding="utf-8") as f:
        src = f.read()
    if MARKER in src:
        print("already patched")
        return 0
    new = patched(src, code)
    try:  # this python may be older than the hook's (3.12 f-strings): then the compile check is skipped
        compile(src, a.path, "exec")
    except SyntaxError:
        print("note: original does not compile with this python; compile check skipped")
    else:
        compile(new, a.path, "exec")
    if a.check:
        print(
            f"would apply: {a.path} (+{new.count(chr(10)) - src.count(chr(10))} lines, code {code})"
        )
        return 0
    shutil.copy2(a.path, a.path + ".bak-a2")
    with open(a.path, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"patched: {a.path} (backup {a.path}.bak-a2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
