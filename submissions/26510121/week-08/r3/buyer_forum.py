"""New, ledger-backed buyer information; no voting or reputation trades."""
from .state import identity


def schedule(journal,config,book,catalog,now):
    interval=max(300,config.get('buyer_forum_post_interval_seconds',300))
    bucket=int(now//interval)
    if journal.get('buyer_insight_bucket')==bucket:return
    recent=[p for p in journal.events('purchase',1000) if type(p.get('ledger')) is int and
            p.get('sku') in catalog and p.get('value',0)>0 and 0<=now-p.get('paid_at',p['_ts'])<1800]
    recent.sort(key=lambda p:((p['value']-p['price'])/catalog[p['sku']]['list_price'],p['_ts']),reverse=True)
    drafts=[]
    for p in recent:
        if (config.get('buyer_championship_enabled') and
                now-journal.get('buyer_insight_sku:'+p['sku'],-100000)<1800):continue
        product=catalog[p['sku']];observed=book.get(p['sku'],{})
        w=p.get('warranty_hours') or 0
        matched=next((t for t in observed.get('term_prices',[]) if t['grade']==p.get('grade') and
                      t['warranty_hours']==w and t['n']>=3),None)
        if not matched:continue
        refs=list(dict.fromkeys([p['ledger']]+matched.get('refs',[])))[:5]
        if len(refs)<2:continue
        discount=1-p['price']/product['list_price']
        age=(now-p['paid_at'])/3600
        quality=('결제 시 사용기한 검증 완료' if p.get('kind')=='food' else
                 '6시간 품질 결과 대기 중' if age<6 else '6시간 경과; 개별 고장·환불 기록 별도 확인')
        text=(f"[동일 조건 실결제 비교] {product.get('name',p['sku'])} ({p['sku']})\n"
              f"내 정산가 {p['price']:,}, 정가 대비 할인 {discount:.1%}; 등급 {p.get('grade')}, 보증 {w:g}h. "
              f"최근 6h 같은 등급·보증 정산 {matched['n']}건의 중앙값 {matched['median']:,.0f}. "
              f"{quality}. 과거 정산이며 현재 재고·호가나 미래 품질을 보장하지 않습니다.")
        signature=identity(['price',p['ledger'],refs])
        drafts.append((signature,text,refs))
    if config.get('buyer_championship_enabled') and bucket%3==1:
        from .buyer_insights import alternatives
        drafts=list(alternatives(journal,book,catalog,now))+drafts
    if bucket%3==2:
        broken={e.get('unit'):e for e in journal.events('public_broken',20000)}
        for p in journal.events('purchase',1000):
            if p.get('kind')!='goods' or not 21600<=now-p.get('paid_at',now)<=172800:continue
            e=broken.get(p.get('unit'))
            if not e or type(p.get('ledger')) is not int:continue
            seq=e.get('_event_id','').partition(':')[2]
            if not seq.isdigit():continue
            refund=e.get('refunded',0)
            text=(f"[확인된 고장·환불] {p.get('name',p['sku'])} ({p['sku']}), 등급 {p.get('grade')}. "
                  f"실결제 {p['price']:,}, 보증 {(p.get('warranty_hours') or 0):g}h; "
                  f"공개 원장에 고장과 환불 {refund:,}이 기록됐습니다. "
                  '전액 환불된 실패 거래의 Saved 기여는 0입니다. 한 거래의 관측이며 전체 상품 고장률은 아닙니다.')
            drafts.insert(0,(identity(['fault',p['ledger'],seq,refund]),text,[p['ledger'],int(seq)]))
            break
    for signature,text,refs in drafts:
        if journal.get('buyer_insight_used:'+signature):continue
        journal.queue_post({'post':text,'refs':refs,'as':'buyer','priority':6,
                            'expires_at':now+interval},'buyer-insight:'+str(bucket))
        journal.put('buyer_insight_used:'+signature,now)
        if config.get('buyer_championship_enabled'):
            for p in recent:
                if p['ledger'] in refs:journal.put('buyer_insight_sku:'+p['sku'],now)
        journal.put('buyer_insight_bucket',bucket)
        journal.add('buyer_insight_queued',{'kind':'fault' if text.startswith('[확인된') else 'same_terms_price',
                                         'refs':refs,'signature':signature},role='buyer')
        break
