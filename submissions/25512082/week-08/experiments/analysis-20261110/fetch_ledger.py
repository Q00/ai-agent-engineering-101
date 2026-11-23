import json,time,urllib.request,sys
U="https://market.ourolabs.ai/api/ledger?after=%d"
def get(a):
    for i in range(3):
        try:
            with urllib.request.urlopen(U%a,timeout=30) as r: return json.load(r)
        except Exception as e: time.sleep(2)
    return []
tail=get(-500); top=tail[-1]['seq']; now=tail[-1]['ts']
out=list(tail)
# full last hour
a=tail[0]['seq']-501
while True:
    rows=get(a); out+=rows; time.sleep(0.15)
    if not rows or rows[0]['ts']<now-3600: break
    a-=500
full_lo=min(r['seq'] for r in out)
# 24h samples: estimate seq rate
rate=(top-full_lo)/(now-min(r['ts'] for r in out))
samples=[]
for k in range(1,97):
    t=now-3600-k*15*60+0  # windows every 15 min back to 25h
    s=int(top-(now-t)*rate)
    rows=get(s); samples+=rows; time.sleep(0.15)
json.dump({'now':now,'top':top,'hour':out,'samples':samples,'rate':rate},open('ledger_dump.json','w'),ensure_ascii=False)
print(len(out),len(samples),rate)
