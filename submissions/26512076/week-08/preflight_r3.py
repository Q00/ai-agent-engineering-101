"""Check local dependencies/identity/kit without printing secrets. --model makes ONE real API request."""
import argparse
import ast
import asyncio
import importlib.util
import os
from pathlib import Path
import r3_common as c


def main():
    p=argparse.ArgumentParser();p.add_argument('--model',action='store_true');a=p.parse_args()
    errors=[]
    for name in ('openai','httpx','a2a','uvicorn','cryptography'):
        ok=importlib.util.find_spec(name) is not None
        print(('OK ' if ok else 'FAIL ')+name)
        if not ok:errors.append(name)
    if importlib.util.find_spec('cryptography'):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        for name in ('shop_key.pem','buyer_key.pem'):
            try:
                key=serialization.load_pem_private_key((c.ROOT/name).read_bytes(),password=None)
                if not isinstance(key,Ed25519PrivateKey):raise ValueError('Not Ed25519')
                print('OK '+name+' (existing Ed25519 identity)')
            except Exception:
                print('FAIL '+name+' missing or invalid; copy your existing local key, do not regenerate')
                errors.append(name)
    kit=c.ROOT.parents[2]/'scripts'/'market_kit.py'
    try:
        tree=ast.parse(kit.read_text(encoding='utf-8-sig'))
        version=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                     and any(isinstance(t,ast.Name) and t.id=='KIT_VERSION' for t in n.targets))
        if version!='4':raise ValueError('Not kit 4')
        print('OK market_kit 4')
    except Exception:
        print('FAIL expected kit v4 at '+str(kit));errors.append('kit')
    print('Configured model: '+c.MODEL)
    if not os.getenv('GEMINI_API_KEY'):
        print('FAIL GEMINI_API_KEY is not set in THIS terminal');errors.append('key env')
    if errors:raise SystemExit('Preflight failed; resolve: '+', '.join(errors))
    if a.model:
        result=asyncio.run(c.ask('preflight','Return {"ready":true}.',{'purpose':'connectivity only'}))
        if result.get('ready') is not True:raise SystemExit('Model did not return ready:true')
        print('OK real model request. This is NOT a live market/settlement test.')
    else:print('Local preflight passed. Use --model to check the actual configured model once.')


if __name__=='__main__':main()
