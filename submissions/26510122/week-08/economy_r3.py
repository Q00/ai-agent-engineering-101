"""Separate optional life planning from the time-sensitive shop restock hook.

Default execution is ordinary restocking. --life-only is an explicit local
planner invocation, never configured in the running seller service.
"""
import argparse
import json
from pathlib import Path

from buyer_life import BuyerLife,load
import restock


def run(state):
    inventory=load(state/'inventory.json',{})
    if inventory.get('round')!=3:return {'buy':[]}
    return restock.run(state)


def run_life(state):
    world=BuyerLife(market_dir=state)
    catalog=load(state/'catalog.json',{})
    if world.ensure(catalog):world.direct_situation()
    return world.tick(catalog)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--life-only',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run_life(args.state) if args.life_only else run(args.state),ensure_ascii=False))
