"""Prepare reviewed chart and safe, aggregate source metadata from frozen inputs."""
from datetime import datetime, timezone, timedelta
import json
import sys
from r3.state import ROOT, read_json

r=read_json(ROOT/sys.argv[1]);KST=timezone(timedelta(hours=9))
label=datetime.fromtimestamp(r['cutoff'],KST).strftime('%Y%m%d-%H%M')
points=[]
for comp in r['comparisons']:
    view=comp['before']
    points.append({'시각':comp['start_kst'],'우리 합계':view['players']['own']['total'],
                   '1위 합계':view['players']['leader']['total']})
points.append({'시각':r['cutoff_kst'],'우리 합계':r['current']['players']['own']['total'],
               '1위 합계':r['current']['players']['leader']['total']})
chart={'id':'evening-progress-'+label,'title':'Buyer가 회복했지만 1위와의 격차는 줄지 않음',
       'description':'11월 10일 KST 공식 보드 4개 관측. 합계 축 1.55–1.80으로 확대. 관측점 사이 선은 추세 표시이며 인과 효과나 예측이 아닙니다.',
       'height':270,'spec':{'type':'line','x':'시각','y':['우리 합계','1위 합계'],
       'yLabel':'전체 시장 합계 점수','yDomain':[1.55,1.8],'startAtZero':False,
       'showValues':True,'showLegend':True,'valueFormat':{'style':'decimal','maximumFractionDigits':3},
       'colors':{'우리 합계':'#167D9A','1위 합계':'#B55D22'}},'rows':points}
chartpath=ROOT/'logs'/('evening-progress-chart-'+label+'.json')
charttext=json.dumps(chart,ensure_ascii=False,indent=2)
if chartpath.exists():
    assert chartpath.read_text(encoding='utf-8')==charttext
else:
    chartpath.write_text(charttext,encoding='utf-8')
current=r['current'];before=r['comparisons'][0]['before']
ranking_rows=[{'역할':role,'오전 점수':before[key]['own'][metric],'현재 점수':current[key]['own'][metric],
    '오전 순위':before[key]['rank'],'현재 순위':current[key]['rank'],'현재 모집단':current[key]['population']}
    for role,key,metric in [('Shop','shops','all'),('Buyer','buyers','all'),('합계','players','total')]]
f=r['buyer_windows'];conversion_rows=[]
for period in ['before_equal_duration','since_framework']:
    for kind in ['food','goods']:
        s=f[period]['segments'][kind]
        conversion_rows.append({'구간':period,'종류':kind,'완료 주문':s['closed'],'결제':s['paid'],
             '결제율':s['conversion'],'상한 초과 견적':s['above_cap'],'전체 견적':s['quotes']})
sql='SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid'
source_files=[c['source'] for c in r['comparisons']]+[r['source']]
payload={'schemaVersion':1,'items':[
    {'id':'official-progress','title':'전체 순위와 1위 격차','queries':[{
        'id':'frozen-boards','source':{'label':'공식 시장 보드 저장본','files':source_files,
        'links':[{'label':'공식 순위판 API','href':'https://market.ourolabs.ai/api/board'}],
        'metricDefinitions':[{'label':'전체 순위','definition':'공식 점수가 숫자인 전체 참가자 중 더 높은 점수의 수+1. house 포함, 동점 공동 순위.'}],
        'filters':['참가자: seochanit','전체 시장 점수와 학생 점수를 분리'],
        'caveats':['모집단은 오전 Shop 64·Buyer 61·합계 67에서 현재 65·62·68로 변했습니다.',
                   '실시간 순위는 보드 조회 시점마다 변합니다. 표와 차트는 표시된 cutoff의 동결본을 사용합니다.']},
        'reportingPeriod':r['comparisons'][0]['start_kst']+' ~ '+r['cutoff_kst'],
        'columns':list(ranking_rows[0]),'rows':ranking_rows,
        'preview':{'kind':'aggregate','note':'전체 보드에서 선택한 우리 계정의 역할별 집계','totalRows':3}}]},
    {'id':'buyer-conversion','title':'동일 길이 구간의 Buyer 결제율','queries':[{
        'id':'closed-order-join','source':{'label':'로컬 append-only 사건 원장','files':['.market/r3.sqlite3','review_evening_progress_20261110.py'],
        'sql':sql,'filters':['kind별 buyer_order·market_result·purchase·quote_comparison 조회',
             '동일 order로 연결, 조회 구간 내 주문과 완료 결과 모두 존재해야 완료 주문으로 집계',
             '직전/직후 각각 '+str(round(f['since_framework']['hours'],5))+'시간, 16:18:54 KST 런타임 시작을 경계로 비교'],
        'metricDefinitions':[{'label':'결제율','definition':'완료 결과가 있는 주문 중 purchase 사건이 있는 주문 비율. 반복 견적·시도 수는 주문 수와 다릅니다.'}],
        'caveats':['시장 장애와 상대 가게·상품 구성이 달라, 전후 차이는 v13 코드의 인과 효과가 아닙니다.',
                   '새 공산품 구매 13개는 6시간 품질 관측 전이므로 최종 Saved 개선을 판정하지 않습니다.']},
        'columns':list(conversion_rows[0]),'rows':conversion_rows,
        'preview':{'kind':'aggregate','note':'2개 구간 × 식품·공산품 집계','totalRows':4}}]},
    {'id':'shop-book-reconciliation','title':'Book 증가와 현금·재고','queries':[{
        'id':'sales-refunds','source':{'label':'공식 자산 변화와 로컬 정산 원장','files':[r['source'],r['comparisons'][0]['source'],'.market/r3.sqlite3'],
        'sql':sql,'filters':['판매: kind=shop_settled, 오전 보드 시점 이후부터 현재 보드 cutoff까지',
            '환불: kind=public_broken, 우리 정산 unit에 연결되며 refunded>0, unit별 중복 제거'],
        'metricDefinitions':[{'label':'Book 증가','definition':'현재 공식 Book − 오전 공식 Book.'},
            {'label':'관측 환불','definition':'구간 중 원장에 반영된 우리 unit 환불액. 이전 구간 판매의 환불도 포함.'}],
        'caveats':['매출총이익 − 관측 환불 − 공식 Book 증가의 잔차 원인을 전부 보관료로 확정하지 않았습니다.',
                   '현금·재고와 KV 자금 모델은 각각 기록된 조회 시각의 상태이며 과거 상태를 복원한 값이 아닙니다.']},
        'columns':['Book 증가','판매','원가 미만 판매','관측 환불액','잔차'],
        'rows':[{'Book 증가':current['shops']['own']['book']-before['shops']['own']['book'],
                 '판매':r['shop_windows']['since_morning']['sales'],
                 '원가 미만 판매':r['shop_windows']['since_morning']['below_cost'],
                 '관측 환불액':r['shop_windows']['since_morning']['observed_refund_amount'],
                 '잔차':r['shop_windows']['since_morning']['margin_minus_observed_refunds']-(current['shops']['own']['book']-before['shops']['own']['book'])}],
        'preview':{'kind':'aggregate','note':'오전 08:58부터 현재 cutoff까지 정산 및 공식 보드 대사','totalRows':1}}]},
    {'id':'framework-maturity','title':'프레임워크 작동과 검증 한계','queries':[{
        'id':'runtime-and-learning','source':{'label':'현재 런타임 및 읽기 전용 프레임워크 검증','files':[
            'logs/framework-live-20261110T105742*.json',sys.argv[1]],
        'filters':['런타임 PID 16608 시작 이후 관측','정책 배정·실패 피드백과 실제 결제를 구분'],
        'metricDefinitions':[{'label':'shadow','definition':'관측·자료 수집 중이며 별점 예측을 가격 결정에 적용하지 않는 상태.'}],
        'caveats':['603개 주문 배정·458개 계약 피드백·9회 경로 제외가 실제 관측됐지만, 성능 최적성을 증명하지 않습니다.',
                   '계정 연결 리뷰는 14개 계정에 걸쳐 수집됐으며 별점 모델은 holdout 검증 전입니다.',
                   '제어 상태 표본 빈도는 오프라인 시간 비율이 아닙니다.']}}]}]}
# Keep exact existing input identities, not wildcard source labels.
actual=sorted((ROOT/'logs').glob('framework-live-20261110T105742*.json'))
assert actual, 'Actual framework verification input is required'
payload['items'][3]['queries'][0]['source']['files'][0]=actual[0].relative_to(ROOT).as_posix()
path=ROOT/'logs'/('evening-progress-sources-'+label+'.json')
path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
print('CHART',chartpath);print('SOURCES',path)
