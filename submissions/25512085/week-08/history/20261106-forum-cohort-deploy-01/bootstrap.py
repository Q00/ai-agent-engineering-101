from pathlib import Path
OUT=Path(__file__).resolve().parent
OLD=OUT.parent/'20261106-gap35-deploy-01'
def put(n,s):
    with (OUT/n).open('x',encoding='utf-8',newline='\n') as f:f.write(s)
s=(OLD/'control.py').read_text()
s=s.replace("'edfb50e5a36815ff131d3ea30d67389758b1d4c8'","'594e1f9bd4de1662c46e4816fabe5a096ab1233d'")
put('control.py',s)
s=(OLD/'run_isolated.py').read_text().replace("names = [", "names = ['test_forum_intel_offline','test_forum_quality_evidence_offline','test_seller_cash_offline','test_sku_break_rates_offline','test_hub_compaction_offline',")
put('run_isolated.py',s)
s=(OLD/'stage.py').read_text().replace('20261106-gap35-runtime-01','20261106-forum-cohort-runtime-01')
put('stage.py',s)
