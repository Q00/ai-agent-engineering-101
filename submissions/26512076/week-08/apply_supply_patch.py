"""Run from repository root with the kit stopped; patches only the known v4 supply loop."""
from pathlib import Path
import argparse
import re
import shutil
import os
import time

OLD='''            if r.status_code == 429:
                time.sleep(min(float(r.json().get("wait", 10)), 30))
                continue'''
NEW='''            if r.status_code == 429:
                # specialty_supply_window_v1: record server timing, do not spin during closure.
                info = r.json()
                wait = max(1.0, float(info.get("wait", 10)))
                opens = float(info.get("opens_at") or (time.time() + wait))
                window = {"opens_at": opens, "closes_at": info.get("closes_at"),
                          "observed_at": time.time(), "reason": info.get("error", "supply limited")}
                write_json(md / "supply_window.json", window)
                if opens > time.time() + 30 or "closed" in str(info.get("error", "")).lower():
                    log(f"[supply] waiting for server restock window, opens_at {opens}")
                    return
                time.sleep(min(wait, 30))
                continue'''

def patch_source(source):
    if not re.search(r'KIT_VERSION\s*=\s*[\"\x27]4[\"\x27]', source):
        raise ValueError('Expected kit v4; no file changed')
    start=source.index('def restock(agent, d, market, md, inv, demand):')
    end=source.index('\ndef broken_news(',start)
    block=source[start:end]
    if 'specialty_supply_window_v1' in block:return source
    if block.count(OLD)!=1:
        raise ValueError('Supply loop differs from known v4; no file changed')
    result=source[:start]+block.replace(OLD,NEW)+source[end:]
    compile(result,'market_kit.py','exec')
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--kit',default='scripts/market_kit.py')
    args=parser.parse_args();path=Path(args.kit)
    source=path.read_text(encoding='utf-8-sig')
    result=patch_source(source)
    if result==source:print('OK: supply patch already installed');return
    backup=path.with_name(path.name+f'.before-specialty-{time.time_ns()}.bak')
    shutil.copy2(path,backup)
    tmp=path.with_name(path.name+'.specialty.tmp');tmp.write_text(result,encoding='utf-8')
    os.replace(tmp,path)
    print('OK: supply window patch installed. Backup:',backup)

if __name__=='__main__':main()
