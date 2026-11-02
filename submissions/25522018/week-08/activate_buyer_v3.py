#!/usr/bin/env python3
"""Reversible Buyer v3 registration without overwriting existing buyer.py.

Usage: python activate_buyer_v3.py --enable | --disable
"""
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
MARKET_BUYER = ROOT / "market_buyer_r3_v2.py"
AGENT = ROOT / "agent.json"
V2_IMPORT = "from buyer import buy"
V3_IMPORT = "from buyer_adaptive import buy"
BASELINE_CMD = "python buyer.py --url {url} --item {item} --budget {budget}"
ADAPTIVE_CMD = "python buyer_adaptive.py --url {url} --item {item} --budget {budget}"


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--enable', action='store_true')
    g.add_argument('--disable', action='store_true')
    args = ap.parse_args()

    required = ['buyer.py', 'llm_harness.py', 'market_buyer_r3_v2.py', 'buyer_adaptive.py', 'agent.json']
    missing = [name for name in required if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit(f'Missing files, no changes: {missing}')
    before_src = MARKET_BUYER.read_text(encoding='utf-8')
    before_config = AGENT.read_text(encoding='utf-8')
    agent = json.loads(before_config)
    if 'buyer' not in agent or 'market_buyer' not in agent:
        raise SystemExit('Invalid agent.json, no changes')

    if args.enable:
        old, new, cmd = V2_IMPORT, V3_IMPORT, ADAPTIVE_CMD
    else:
        old, new, cmd = V3_IMPORT, V2_IMPORT, BASELINE_CMD

    if new in before_src and old not in before_src:
        new_src = before_src
    elif before_src.count(old) == 1 and new not in before_src:
        new_src = before_src.replace(old, new, 1)
    else:
        raise SystemExit('Unexpected market_buyer_r3_v2.py import; no changes')
    agent['buyer']['cmd'] = cmd
    # Market selector itself stays Buyer v2; it imports the new negotiation loop.
    new_config = json.dumps(agent, indent=2, ensure_ascii=False) + '\n'

    if new_src == before_src and new_config == before_config:
        print('Already in requested state; no changes')
        return

    # Backups OUTSIDE the repo to avoid accidentally adding them to a PR.
    repo = ROOT.parents[2]  # D:\ai-agent-week08
    backup = repo.parent / f'{repo.name}-buyer-v3-backup'
    backup.mkdir(parents=True, exist_ok=True)
    for src in (MARKET_BUYER, AGENT):
        target = backup / src.name
        if not target.exists():
            shutil.copy2(src, target)

    MARKET_BUYER.write_text(new_src, encoding='utf-8')
    AGENT.write_text(new_config, encoding='utf-8')
    print(f'Buyer v3 {"ENABLED" if args.enable else "DISABLED"}.')
    print(f'Buyer hook: {cmd}')
    print(f'Backups outside repository: {backup}')
    print('The original buyer.py, signing keys, seller, restock and review hooks were not changed.')


if __name__ == '__main__':
    main()
