"""Install logging in known kit v4 without modifying checkout/settlement decisions."""
import argparse
import ast
import os
from pathlib import Path
import re
import shutil
import time

MARKER='# shop_checkout_trace_v1'
def patch_source(source):
    if not re.search(r'KIT_VERSION\s*=\s*[\"\x27]4[\"\x27]',source):raise ValueError('Expected kit v4')
    if MARKER in source:return source
    tree=ast.parse(source)
    for name,method in [('AcpShop','handle'),('Proxy','observe')]:
        cls=next((n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name),None)
        if cls is None or not any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==method for n in cls.body):
            raise ValueError('Unrecognized kit; no changes made')
    entry=next((n for n in tree.body if isinstance(n,ast.If) and isinstance(n.test,ast.Compare) and isinstance(n.test.left,ast.Name) and n.test.left.id=='__name__'),None)
    if entry is None:raise ValueError('Cannot find kit main entry')
    lines=source.splitlines(keepends=True)
    snippet='\n'+MARKER+'\nfrom checkout_trace import install as _install_checkout_trace\n_install_checkout_trace(globals())\n\n'
    lines.insert(entry.lineno-1,snippet);out=''.join(lines);compile(out,'market_kit.py','exec');return out

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--kit',default='scripts/market_kit.py');a=parser.parse_args()
    kit=Path(a.kit);source=kit.read_text(encoding='utf-8-sig');updated=patch_source(source)
    helper=Path(__file__).with_name('checkout_trace.py');dest=kit.with_name('checkout_trace.py')
    contents=helper.read_text(encoding='utf-8');compile(contents,str(dest),'exec')
    if dest.exists() and dest.read_text(encoding='utf-8')!=contents:
        shutil.copy2(dest,dest.with_name(dest.name+f'.before-trace-{time.time_ns()}.bak'))
    tmp=dest.with_name(dest.name+'.tmp');tmp.write_text(contents,encoding='utf-8');os.replace(tmp,dest)
    if updated!=source:
        backup=kit.with_name(kit.name+f'.before-checkout-trace-{time.time_ns()}.bak');shutil.copy2(kit,backup)
        tmp=kit.with_name(kit.name+'.trace.tmp');tmp.write_text(updated,encoding='utf-8');os.replace(tmp,kit)
        print('OK: checkout trace installed. Backup:',backup)
    else:print('OK: checkout trace already installed')
if __name__=='__main__':main()
