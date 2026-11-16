"""New, ledger-backed buyer information; no voting or reputation trades."""
from .state import identity


def schedule(journal,config,book,catalog,now):
    interval=max(300,config.get('buyer_forum_post_interval_seconds',300))
    bucket=int(now//interval)
    if journal.get('buyer_insight_bucket')==bucket:return
    focus='balanced';policy_id=None
    if config.get('objective_decision_enabled'):
        from .feedback import get
        operating=get(journal,config,now)
        if operating:
            focus=operating.get('patches',{}).get('buyer',{}).get('buyer_forum_focus','balanced')
            policy_id=operating['id']
    effective_focus=focus if bucket%5 else 'balanced'
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
        drafts.append((signature,text,refs,'same_terms_price'))
    if config.get('buyer_championship_enabled') and (bucket%3==1 or effective_focus=='acceptable_alternatives'):
        from .buyer_insights import alternatives
        drafts=[(*row,'acceptable_alternatives') for row in alternatives(journal,book,catalog,now)]+drafts
    if bucket%3==2 or effective_focus=='mature_quality':
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
            drafts.insert(0,(identity(['fault',p['ledger'],seq,refund]),text,[p['ledger'],int(seq)],'mature_quality'))
            break
    if config.get('objective_decision_enabled'):
        # One in five slots retains the baseline mix; missing evidence never
        # causes a fabricated draft or blocks another valid information format.
        if effective_focus!='balanced':
            drafts.sort(key=lambda row:row[3]!=effective_focus)
    audience=journal.get('buyer-insight-audience:v28',{}) if config.get('reversal_v28_enabled') else {}
    ref_category={p.get('ledger'):catalog.get(p.get('sku'),{}).get('category') for p in journal.events('purchase',1000)} if config.get('reversal_v28_enabled') else {}
    if config.get('reversal_v28_enabled'):
        def category_of(row):return next((ref_category[r] for r in row[2] if ref_category.get(r)),None)
        drafts.sort(key=lambda row:((row[3]!=effective_focus) if effective_focus!='balanced' else False,
            audience.get(category_of(row),0)))
    requested=set()
    if config.get('score_campaign_enabled') and config.get('finalday_departments_enabled'):
        from .score_campaign import public_demand_categories
        requested=public_demand_categories(journal,now)
        drafts.sort(key=lambda row:not any(ref_category.get(ref) in requested for ref in row[2]))
    for signature,text,refs,information_kind in drafts:
        if journal.get('buyer_insight_used:'+signature):continue
        if any(ref_category.get(ref) in requested for ref in refs):
            text='[최근 공개 수요 관련] '+text
        journal.queue_post({'post':text,'refs':refs,'as':'buyer','priority':6,
                            'expires_at':now+interval,'information_kind':information_kind,
                            'policy_id':policy_id},'buyer-insight:'+str(bucket))
        journal.put('buyer_insight_used:'+signature,now)
        if config.get('reversal_v28_enabled'):
            category=category_of((signature,text,refs,information_kind))
            if category:
                audience[category]=now
                journal.put('buyer-insight-audience:v28',dict(sorted(audience.items(),key=lambda p:-p[1])[:32]))
        if config.get('buyer_championship_enabled'):
            for p in recent:
                if p['ledger'] in refs:journal.put('buyer_insight_sku:'+p['sku'],now)
        journal.put('buyer_insight_bucket',bucket)
        journal.add('buyer_insight_queued',{'kind':information_kind,
                                         'refs':refs,'signature':signature},role='buyer')
        if config.get('objective_decision_enabled'):
            journal.add('buyer_forum_policy_choice',{'policy_id':policy_id,'focus':focus,
                'chosen_kind':information_kind,'refs':refs,'available_drafts':len(drafts),
                'baseline_exploration_slot':bucket%5==0},role='buyer')
        break
