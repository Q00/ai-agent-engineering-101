"""Install the Round 3 full-stock snapshot hook in the known market kit v4."""
import argparse
import os
from pathlib import Path
import re
import shutil
import time

MARKER = 'r3_market_stock_snapshot_v1'
ANCHOR = '    if not shops:\n'
SNAPSHOT = '''    # r3_market_stock_snapshot_v1: capture full stock before narrowing offers to this order.
    if rnd >= ECON_ROUND:
        try:
            stock_path = d / ".market" / "market_stock.json"
            stock_path.parent.mkdir(exist_ok=True)
            write_json(stock_path, {"observed_at": time.time(), "shops": [
                {"name": shop.get("name"), "url": shop.get("url"), "stock": shop.get("stock")}
                for shop in shops
            ]})
        except OSError as exc:
            log(f"[kit] market stock snapshot unavailable: {type(exc).__name__}")
'''


def patch_source(source):
    if not re.search(r'\bKIT_VERSION\s*=\s*(["\x27])4\1', source):
        raise ValueError('Expected market kit v4. The file was not changed.')
    start = source.index('def handle_order(')
    end = source.index('\ndef ', start)
    block = source[start:end]
    if MARKER in block:
        return source
    if (block.count(ANCHOR) != 1 or '"/api/shops"' not in block
            or 'def write_json(' not in source or 'ECON_ROUND =' not in source):
        raise ValueError('Unknown market kit layout. The file was not changed.')
    result = source[:start] + block.replace(ANCHOR, SNAPSHOT + ANCHOR, 1) + source[end:]
    compile(result, 'market_kit.py', 'exec')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kit', default='scripts/market_kit.py')
    path = Path(parser.parse_args().kit)
    try:
        source = path.read_text(encoding='utf-8-sig')
        updated = patch_source(source)
        if source == updated:
            print('OK: market stock snapshot already installed')
            return
        backup = path.with_name(f'{path.name}.before-market-stock-{time.time_ns()}.bak')
        shutil.copy2(path, backup)
        temporary = backup.with_suffix('.tmp')
        try:
            temporary.write_text(updated, encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, SyntaxError) as exc:
        parser.exit(1, f'Market stock patch failed: {exc}\n')
    print(f'OK: market stock snapshot installed. Backup: {backup}')


if __name__ == '__main__':
    main()
