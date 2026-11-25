"""Render local simulation evidence as a self-contained review page and CSV."""
import argparse
import csv
from datetime import datetime
from html import escape
import json
from pathlib import Path

LABELS={'cooked':'조리','placed':'가구 배치','meal_planned':'레시피 변경','forum_drafted':'내부 초안 저장',
        'furniture_broken':'가구 고장','food_expired':'식품 기한 만료'}
STATUSES={'available':'보유 중','consumed':'가상 조리에 사용','broken':'고장','expired':'기한 만료'}


def e(value):return escape(str(value if value is not None else ''))
def money(value):return f'{value:,}'


def render(source,output,csv_path):
    x=json.loads(source.read_text());m=x['metrics'];events=x['tool_events']
    units=x['final_state']['owned'];refunds={r['unit']:r['amount'] for r in x['refunds']}
    effects={unit:[] for unit in units}
    for event in events:
        for unit in event.get('ingredients',[]):effects.setdefault(unit,[]).append('조리 사용')
        if event.get('unit_id') and event['event'] in LABELS:
            effects.setdefault(event['unit_id'],[]).append(LABELS[event['event']])
    csv_path.parent.mkdir(parents=True,exist_ok=True)
    with csv_path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=['step','sim_clock','scene','item','sku','shop','signed_receipt',
            'paid','quoted_price','charged','reason','simulated_ledger','refund','final_status','simulated_only'])
        writer.writeheader()
        for r in x['steps']:
            p=r['payment'];settled=(r.get('deal') or {}).get('settled',{});unit=settled.get('unit')
            writer.writerow({'step':r['step'],'sim_clock':r['sim_clock'],'scene':r['scene']['title'],
                'item':r['requested_product'],'sku':r['sku'],'shop':p['shop'],'signed_receipt':r['signed_receipt'],
                'paid':p['paid'],'quoted_price':p['price'],'charged':p['price'] if p['paid'] else 0,
                'reason':p['reason'],'simulated_ledger':p['ledger'],'refund':refunds.get(unit,0),
                'final_status':units.get(unit,{}).get('status'),'simulated_only':True})
    cards=[]
    for r in x['steps']:
        p=r['payment'];plan=r.get('plan_after') or {};decision=r.get('decision') or {}
        applied=[v for v in r['new_events'] if v['event'] in LABELS]
        rejected=[v for v in r['new_events'] if v['event'] in ('life_action_rejected','life_plan_rejected','preference_revision_rejected')]
        clock=datetime.fromisoformat(r['sim_clock']).strftime('%m.%d %H:%M')
        actions=''.join(f'<li><b>{e(LABELS[v["event"]])}</b> {e(v.get("summary"))}</li>' for v in applied)
        if not actions:actions='<li>도구로 반영된 조리·배치·초안 없음. 대기는 상태 변경 없이 기록됩니다.</li>'
        refund=r.get('refund')
        refund_html=f'<div class="refund">고장·환불 사건 · {money(refund["amount"])} 크레딧 반환</div>' if refund else ''
        error_html=''.join(f'<li>{e(v.get("error"))}</li>' for v in rejected)
        transaction=(f'모의 결제 완료 · {money(p["price"])} 크레딧' if p['paid'] else f'결제 차단 · 청구 0 · {e(p["reason"])}')
        cards.append(f'''<article class="episode" data-blocked="{int(not p['paid'])}" data-refund="{int(bool(refund))}">
          <div class="time"><b>{clock}</b><span>STEP {r['step']:02d} · 가상 시각</span></div>
          <div class="card"><div class="topline"><span class="tag">외부 사실</span><span>{e(r['scene']['title'])}</span></div>
          <h2>{e(' / '.join(r['scene']['facts']))}</h2>
          <div class="purchase {'paid' if p['paid'] else 'blocked'}"><small>발행된 모의 주문</small><strong>{e(r['requested_product'])}</strong><span>{transaction}</span></div>
          {refund_html}
          <div class="decision"><small>인물의 해석 · 모델이 쓴 자기설명</small><p>{e(plan.get('interpretation') or decision.get('reason') or '판단 응답 없음')}</p></div>
          <div class="intent"><span>{e(plan.get('update') or '—')}</span><b>{e(plan.get('intention'))}</b></div>
          <div class="actual"><small>실제로 반영된 가상 행동</small><ul>{actions}</ul></div>
          <footer>허기 {r['hunger_before']:.1f} → {r['hunger_after']:.1f} / 100 <span>모델 호출 {len(r['model_calls'])}회</span></footer>
          <details><summary>계획·거절 결과 보기</summary><p>{e(decision.get('reason'))}</p><ol>{''.join('<li>'+e(t)+'</li>' for t in plan.get('next_steps',[]))}</ol>
          <ul class="errors">{error_html or '<li>거절된 도구 행동 없음</li>'}</ul></details></div></article>''')
    purchases=[]
    for r in x['steps']:
        d=r.get('deal')
        if not d:continue
        s=d['settled'];unit=s['unit'];item=units[unit]
        purchases.append(f'<tr><td>{e(s["name"])}<small>{e(s["sku"])}</small></td><td>{money(d["receipt"]["price"])}</td><td>{money(refunds.get(unit,0))}</td><td>{e(" · ".join(dict.fromkeys(effects.get(unit,[]))) or "사용 기록 없음")}</td><td>{e(STATUSES.get(item["status"],item["status"]))}</td></tr>')
    failures=sum(1 for v in events if v['event'] in ('life_action_rejected','life_plan_rejected','preference_revision_rejected'))
    body=f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>내부 생활·구매 시뮬레이션</title><style>
    :root{{color-scheme:light;--ink:#24342c;--muted:#67746c;--line:#dce3da;--green:#235c45;--red:#a54435}}
    *{{box-sizing:border-box}}body{{margin:0;background:#f4f5ef;color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Noto Sans KR',sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 26px 72px}}h1{{font-size:40px;line-height:1.2;letter-spacing:-1.5px;margin:14px 0 18px}}h2{{font-size:19px;line-height:1.5;margin:12px 0 22px;word-break:keep-all}}p{{margin:8px 0}}small{{display:block;color:var(--muted);font-size:12px;letter-spacing:.2px}}.eyebrow{{color:var(--green);font-weight:750;letter-spacing:2px;font-size:12px}}.lead{{max-width:760px;color:var(--muted);font-size:17px}}.badge{{display:inline-block;border:1px solid var(--green);color:var(--green);border-radius:30px;padding:5px 12px;font-size:12px}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:28px 0}}.stat{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:19px}}.stat b{{display:block;font-size:29px;line-height:1.5}}.note{{border-left:3px solid #b89a58;background:#eceee3;padding:16px 20px;margin:20px 0 32px}}nav{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}}button{{font:inherit;cursor:pointer;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:24px;padding:7px 15px}}button[aria-pressed=true]{{background:var(--green);color:white;border-color:var(--green)}}button:focus-visible,summary:focus-visible{{outline:3px solid #dba85c;outline-offset:3px}}.episode{{display:grid;grid-template-columns:138px 1fr;gap:22px;margin-bottom:24px;position:relative}}.time{{padding-top:20px}}.time b{{display:block;font-size:17px}}.time span{{display:block;color:var(--muted);font-size:12px}}.card{{border:1px solid var(--line);border-radius:20px;padding:25px 28px;background:#fff;min-width:0}}.topline{{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:13px}}.tag{{background:#edf1e8;color:var(--green);border-radius:5px;padding:2px 8px}}.purchase{{background:#edf5ef;padding:13px 16px;border-radius:11px;display:grid;gap:4px}}.purchase strong{{overflow-wrap:anywhere}}.purchase span{{font-size:13px;color:var(--green)}}.blocked{{background:#fcf0e9}}.blocked span{{color:var(--red);overflow-wrap:anywhere}}.refund{{margin-top:10px;padding:10px 15px;border-radius:10px;background:#f9eed3;color:#79550b;font-weight:650}}.decision{{margin-top:22px}}.decision p{{word-break:keep-all}}.intent{{display:flex;align-items:flex-start;gap:10px;margin:17px 0}}.intent span{{background:#edf0f7;color:#53637b;border-radius:5px;padding:2px 7px;font-size:11px}}.actual ul{{margin:7px 0;padding-left:21px}}.actual li{{margin-bottom:7px;font-size:13px;overflow-wrap:anywhere}}.actual li b{{color:var(--green)}}footer{{display:flex;justify-content:space-between;gap:14px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:13px;margin-top:17px}}details{{margin-top:14px;color:var(--muted);font-size:13px}}summary{{cursor:pointer}}.errors{{color:var(--red)}}.table-wrap{{overflow-x:auto;background:white;border:1px solid var(--line);border-radius:16px}}table{{width:100%;border-collapse:collapse;min-width:680px;font-size:13px}}th,td{{padding:15px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{background:#eaf0e6;color:var(--green)}}td:first-child{{max-width:440px}}td small{{font-size:10px;margin-top:4px}}.section-title{{margin-top:40px;font-size:24px}}.fine{{color:var(--muted);font-size:12px;margin-top:15px}}[hidden]{{display:none!important}}
    @media(max-width:650px){{main{{padding:28px 16px 48px}}h1{{font-size:29px}}.lead{{font-size:15px}}.stats{{grid-template-columns:repeat(2,1fr)}}.stat{{padding:14px}}.stat b{{font-size:25px}}.episode{{display:block}}.time{{padding:0 0 8px;display:flex;gap:10px;align-items:baseline}}.card{{padding:19px}}h2{{font-size:18px}}footer{{flex-wrap:wrap}}}}
    </style></head><body><main>
    <div class="eyebrow">INTERNAL SIMULATION / ONE RESIDENT</div><h1>한 대학원생의 1박 2일</h1><span class="badge">{e(x['resident'])} · 가상 {x['simulated_hours']}시간 · 빈 재고에서 시작</span>
    <p class="lead">물건을 사고, 사용하고, 고장과 일정 변경을 만난 기록입니다. 감정과 대응은 인물 모델이 정했고, 아래에서는 모델의 설명과 도구로 반영된 결과를 구분합니다.</p>
    <div class="stats"><div class="stat"><small>모의 결제 / 주문</small><b>{m['paid']} / {m['orders']}</b></div><div class="stat"><small>차단된 결제</small><b>{m['refused']}</b></div><div class="stat"><small>모의 순지출 · 크레딧</small><b>{money(m['net_spend'])}</b></div><div class="stat"><small>모의 환불 · 크레딧</small><b>{money(m['refunds'])}</b></div></div>
    <div class="note"><b>실제 시장 거래·게시·배포는 없습니다.</b><br>상품명은 카탈로그, 주문·가격·고장은 시뮬레이터가 정했습니다. 가게 선택·협상·생활 반응에는 실제 모델을 사용했습니다. 구매는 발행된 주문을 따른 것이며, 상황 때문에 그 상품을 샀다는 인과관계를 뜻하지 않습니다.</div>
    <nav aria-label="사건 필터"><button data-filter="all" aria-pressed="true">전체 기록</button><button data-filter="blocked" aria-pressed="false">결제 차단</button><button data-filter="refund" aria-pressed="false">고장·환불</button></nav>
    <section id="timeline">{''.join(cards)}</section>
    <h2 class="section-title">결제한 물건은 어떻게 됐나</h2><div class="table-wrap"><table><thead><tr><th>상품</th><th>결제</th><th>환불</th><th>관측한 사용</th><th>최종 상태</th></tr></thead><tbody>{''.join(purchases)}</tbody></table></div>
    <p class="fine">서명 영수증 {m['signed_receipts']}건 / 모의 결제 {m['paid']}건 / 거절된 도구 행동 {failures}건 / 모델 호출 {len(x['model_calls'])}회. 가상 음식 1개는 계량되지 않은 재료 묶음이며, 배치 크기와 허기는 게임 단위입니다. 모델의 자기설명은 행동의 인과관계나 실제 연구 성과를 증명하지 않습니다.</p>
    </main><script>document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));document.querySelectorAll('.episode').forEach(e=>e.hidden=b.dataset.filter!=='all'&&e.dataset[b.dataset.filter]!=='1');}}));</script></body></html>'''
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(body)
    print(json.dumps({'html':str(output),'csv':str(csv_path),'episodes':len(cards),'purchases':len(purchases)},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--csv',type=Path,required=True)
    args=p.parse_args();render(args.input,args.output,args.csv)
