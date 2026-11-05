"""Build reviewed chart/Sources inputs from the frozen peer analysis only."""
import json
import sys
from r3.state import ROOT,read_json


if __name__=='__main__':
    analysis=sys.argv[1] if len(sys.argv)>1 else 'logs/peer-market-analysis-20261105T054631611685Z.json'
    prefix=sys.argv[2] if len(sys.argv)>2 else 'peer-growth'
    result=read_json(ROOT/analysis,{})
    source=read_json(ROOT/result['source'],{})
    window=result['comparison_windows'][-1]
    rows=[]
    for name in ['seochanit','inextro','Kiro729']:
        shop=next(r['changes'] for r in window['roles']['shops'] if r['name']==name)
        buyer=next(r['changes'] for r in window['roles']['buyers'] if r['name']==name)
        rows.append({'participant':name,'shop_book_change':shop['book']['delta']/10000,
            'buyer_surplus_change':buyer['surplus']['delta']/10000,
            'shop_revenue_change':shop['revenue']['delta']/10000,
            'buyer_additional_deals':buyer['deals']['delta']})
        # Reconcile displayed differences independently against the public rows.
        before=read_json(ROOT/window['source'],{})['board']
        after=source['responses']['/api/board']['data']
        for role,metric,field in [('shops','book','shop_book_change'),('buyers','surplus','buyer_surplus_change')]:
            a=next(r for r in before[role] if r['name']==name)
            b=next(r for r in after[role] if r['name']==name)
            assert abs((b[metric]-a[metric])/10000-rows[-1][field])<1e-8
    minutes=round(window['hours']*60)
    chart={'id':'peer-market-growth','title':f'최근 약 {minutes}분의 경쟁자별 증감 (만원)',
        'description':'보존된 이전 board와 현재 공개 board 비교. 단기 관측이며 장기 예측이 아닙니다.',
        'height':320,'spec':{'type':'bar','x':'participant',
            'y':['shop_book_change','buyer_surplus_change'],'xLabel':'참가자',
            'yLabel':'증감 (만원)','showValues':True,
            'valueFormat':{'style':'decimal','maximumFractionDigits':1},
            'colors':{'shop_book_change':'#137C78','buyer_surplus_change':'#5965B8'}},'rows':rows}
    # Reader-facing series names are the actual bindings, not separate aliases.
    for row in rows:
        row['Shop book']=row.pop('shop_book_change');row['Buyer surplus']=row.pop('buyer_surplus_change')
    chart['spec']['y']=['Shop book','Buyer surplus']
    chart['spec']['colors']={'Shop book':'#137C78','Buyer surplus':'#5965B8'}
    receipt={'schemaVersion':1,'items':[{'id':'peer-market-comparison',
        'title':'동일 기간의 경쟁자별 book·매출·surplus 증감',
        'queries':[{'id':'matched-public-board-deltas',
            'source':{'label':'공개 시장 board와 보존된 이전 board',
                'files':[result['source'],window['source'],'compare_market_peers.py'],
                'links':[{'label':'공개 순위표','url':'https://market.ourolabs.ai/api/board'},
                         {'label':'공식 규칙','url':'https://market.ourolabs.ai/agent.md'}],
                'metricDefinitions':[{'label':'Shop book','definition':'시장 board가 공개한 book입니다. 매입 현금을 재고로 전환한 금액은 수익으로 보지 않습니다.'},
                    {'label':'Buyer surplus','definition':'시장 board의 누적 surplus입니다. 과거 구매의 품질 결과도 반영되어 변할 수 있습니다.'}],
                'filters':['두 시점에 같은 이름으로 존재하는 참가자만 증가액을 비교했습니다.',
                    '표시된 세 계정은 우리 계정, 종합 1위, 상위 경쟁자 Kiro729입니다.'],
                'caveats':['이전 board의 정확한 서버 기준 시각은 보존되지 않아 저장 시각을 비교 구간 표시에 사용했습니다.',
                    '공급 주기와 환불이 짧은 구간의 증감에 영향을 줄 수 있어 장기 속도로 외삽하지 않았습니다.',
                    '증가액 차이가 전략의 인과 효과임을 검증한 것은 아닙니다.']},
            'reportingPeriod':f'이전 저장 epoch {window["start"]} → 현재 조회 epoch {window["end"]}',
            'methods':[{'language':'calculation','code':'각 증가액 = 같은 참가자의 현재 공개 누적값 - 이전 공개 누적값; 차트 단위 = 원 / 10,000'}],
            'columns':['participant','Shop book','Buyer surplus','shop_revenue_change','buyer_additional_deals'],
            'rows':rows,'preview':{'kind':'aggregate','note':'비교 대상으로 선택한 세 계정의 집계입니다.'}}]}]}
    for filename,value in [(prefix+'-chart.json',chart),(prefix+'-sources.json',receipt)]:
        path=ROOT/'logs'/filename
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RECONCILIATION_PASS: all six plotted changes match the two raw public boards.')
