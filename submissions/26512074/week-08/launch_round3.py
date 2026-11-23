"""Official kit-v4 launcher. --publish starts the authorized shop and buyer."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent
MARKET='https://market.ourolabs.ai'


def configure():
    os.chdir(ROOT)
    runtime=ROOT/'.runtime-r3'
    if runtime.exists():
        sys.path.insert(0,str(runtime))
    os.environ['PYTHONPATH']=str(runtime)+os.pathsep+os.environ.get('PYTHONPATH','')
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+str(ROOT/'tools')+os.pathsep+os.environ['PATH']
    os.environ['PYTHONIOENCODING']='utf-8'
    spec=importlib.util.spec_from_file_location('official_market_kit',ROOT/'vendor/market-kit/market_kit.py')
    kit=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=kit
    spec.loader.exec_module(kit)
    assert kit.KIT_VERSION=='4'
    return kit


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--publish',action='store_true')
    a=p.parse_args()
    kit=configure()
    md=ROOT/'.market'
    md.mkdir(exist_ok=True)
    previous=ROOT.parent/'week-07'
    for name in ('shop_key.pem','buyer_key.pem'):
        if not (ROOT/name).exists() and (previous/name).exists():
            shutil.copy2(previous/name,ROOT/name)
    claim_path=md/'owner-code.txt'
    if not claim_path.exists() and (previous/'.market/owner-code.txt').exists():
        shutil.copy2(previous/'.market/owner-code.txt',claim_path)
    ready=claim_path.exists() and shutil.which('cloudflared') is not None
    if not a.publish:
        print(json.dumps({'ready':ready,'kit':kit.KIT_VERSION,
                          'claim_available':claim_path.exists(),
                          'tunnel_available':shutil.which('cloudflared') is not None}))
        return 0 if ready else 1
    if not ready:
        raise SystemExit('Owner-code file or cloudflared is missing.')
    with kit.httpx.Client(timeout=20) as client:
        board=client.get(MARKET+'/api/board?round=3')
        board.raise_for_status()
        if board.json().get('current')!=3:
            raise SystemExit('Round 3 is not current; no registration performed.')
    os.environ['ROUND3_FORUM_ENABLED']='1'
    (md/'recommendations-ready.json').write_text('{"enabled":true}',encoding='utf-8')
    (md/'operator.pid').write_text(str(os.getpid()),encoding='ascii')
    # Avoid exposing the owner code in the OS process command line.
    owner=claim_path.read_text(encoding='utf-8-sig').strip()
    sys.argv=['market_kit','join','--dir',str(ROOT),'--market',MARKET,
              '--name','totato2203-r3','--tunnel','--owner',owner]
    kit.main()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
