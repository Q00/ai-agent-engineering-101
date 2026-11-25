import json, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agent_tools, patch_notes, scorecard, meta_harness
backup=json.load(open('meta/evidence/astra-1107-113730-dropped-tools.json'))
for name in ['auto_market_research_bundle','auto_auto_market_research_forum']:
 with tempfile.TemporaryDirectory() as d:
  p=Path(d);(p/'catalog.json').write_text(json.dumps({'G1':{'name':'매트리스','category':'가구','list_price':100}}))
  (p/'ledger_rows.jsonl').write_text(json.dumps({'k':'settle','seq':1,'ts':100,'sku':'G1','amount':90,'buyer':'buyer','shop':'shop'})+'\n')
  (p/'inventory.json').write_text('{}');(p/'forum.json').write_text('[]')
  script=p/'tool.py';script.write_text(backup[f'tools/{name}/tool.py'])
  r=subprocess.run([sys.executable,str(script)],input='{"query":"매트리스"}',text=True,capture_output=True,env={**os.environ,'MARKET_DIR':d,'PYTHONPATH':os.getcwd()})
  print(json.dumps({'kind':'semantic_probe','tool':name,'expected_paid':1,'actual':json.loads(r.stdout) if r.returncode==0 else r.stderr},ensure_ascii=False))
for sku in ['G8ec51935','G03d0199f','Gb5d0dde7','G24f5f979']:
 print(json.dumps({'kind':'supply_audit','sku':sku,'result':agent_tools.execute('supply_audit',{'sku':sku},str(Path('.market').resolve()))},ensure_ascii=False))
p=json.load(open('meta/evidence/astra-1107-113730.json'))
print(json.dumps({'kind':'rules','result':patch_notes.check(page=Path('meta/evidence/astra-1107-113730-rules.html').read_text(),record=False)},ensure_ascii=False))
print(json.dumps({'kind':'baseline','result':meta_harness.since_last(p,'astra',now=1794019052)},ensure_ascii=False))
