"""Semantic fixtures and public objective reconciliation, no operational writes."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import scorecard, agent_tools
from test_market_scan import CAT, ROWS
BASE=Path('meta/evidence/astra-1105-093817')
def emit(**x): print(json.dumps(x,ensure_ascii=False))
old=json.loads(Path(str(BASE)+'-tools-before.json').read_text())
with tempfile.TemporaryDirectory() as d:
 md=Path(d)/'.market';md.mkdir()
 (md/'catalog.json').write_text(json.dumps(CAT));(md/'ledger_rows.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in ROWS))
 for name in ['auto_market_lookup','auto_market_scan']:
  for args in [{'query':'G1'},{}]:
   p=subprocess.run([sys.executable,'-c',old['tools/'+name+'/tool.py']],input=json.dumps(args),capture_output=True,text=True,env={'PATH':os.environ['PATH'],'MARKET_DIR':str(md),'PYTHONPATH':str(Path.cwd())})
   emit(kind='tool_before',name=name,args=args,returncode=p.returncode,result=json.loads(p.stdout) if p.returncode==0 else p.stderr)
 for args in [{'query':'G1'},{}]: emit(kind='tool_after',args=args,result=agent_tools.execute('auto_market_scan',args,str(md)))
b=json.loads(Path(str(BASE)+'-board.json').read_text());o=scorecard.objective(b)
rows=[r for r in b['shops'] if r.get('all') is not None];me=next(r for r in rows if r['name']=='nocha12')
book=sum(w*scorecard.percentile(rows,'book' if ns[0]=='profit' else ns[0],me) for ns,w in scorecard.OBJECTIVE['shops'])
emit(kind='board_reconciliation',objective=o,shop_book_all=round(book,3),cash_profit=me['profit'],book=me['book'],note='live snapshot fetched during review; not the 09:38 pack; cash objective kept per user')
