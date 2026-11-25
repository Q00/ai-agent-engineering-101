import json,datetime,collections
pre='meta/evidence/astra-1110-021612';p=json.load(open(pre+'.json'));now=datetime.datetime.fromisoformat(p['time']).timestamp();start=datetime.datetime.fromisoformat('2026-11-09T13:30:41+09:00').timestamp()
orders={}
for line in open('.market/measure/orders.jsonl'):
 try:r=json.loads(line)
 except:continue
 if start<=r.get('ts',0)<=now:orders[r.get('order')]=r
sales={};broken={}
for line in open('.market/ledger_rows.jsonl'):
 try:r=json.loads(line)
 except:continue
 if not start<=r.get('ts',0)<=now:continue
 if r.get('k')=='settle' and r.get('buyer')=='nocha12':sales[r.get('order')]=r
 if r.get('k')=='broken' and r.get('order'):broken[r['order']]=r
cat=json.load(open('.market/catalog.json'));groups=collections.defaultdict(list)
for order,r in orders.items():
 for key in ['goods_grade','goods_ladder','food_ladder','saved_floor','warranty_said','our_ask']:
  if r.get(key):groups[(key,r[key].get('study'),r[key].get('arm'))].append((order,r))
with open(pre+'-study-arms.jsonl','w') as f:
 for key,rr in sorted(groups.items()):
  paid=[sales[o] for o,r in rr if o in sales];mature=[r for r in paid if r['ts']<=now-21600];value=0;unknown=0
  for r in mature:
   c=cat.get(r.get('sku'),{});b=broken.get(r.get('order'));price=c.get('list_price')
   if not price or c.get('kind')=='food':unknown+=1;continue
   if b:
    w=r.get('warranty');value+=0 if isinstance(w,(int,float)) and 0<=b['ts']-r['ts']<=w*3600 else -1
   else:value+=1-r['amount']/price
  out=dict(study=key[0],id=key[1],arm=key[2],orders=len(rr),paid=len(paid),mature_paid=len(mature),unknown_food_value=unknown,known_normalized_surplus=round(value,6),first_ts=min(r['ts'] for o,r in rr),last_ts=max(r['ts'] for o,r in rr),note='동시등급/사다리처치·수정버전이 겹침. 식품의 사용기한 실제가치는 결측. 이 합은 Saved분모를 포함한 인과효과가 아님.')
  f.write(json.dumps(out,ensure_ascii=False)+'\n');print(out)
