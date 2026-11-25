"""Build a complete per-commit review without altering the harness's append-only journals."""
import collections,json,subprocess
from pathlib import Path
AUDIT='meta/evidence/astra-1107-113730-audit-v3.jsonl'
rows=[(i,json.loads(l),l.rstrip()) for i,l in enumerate(open(AUDIT),1)]
evidence=[];grades=[];keep=[]
def cite(i,excerpt,meaning,path=AUDIT):
 evidence.append({'ref':f'{path}:{i}','excerpt':excerpt,'meaning':meaning})
for i,r,line in rows:
 if r['kind']!='commit':continue
 sha=r['commit'];b,a=r['before'],r['after'];title=r['title'];files=r['files']
 reason=f"전후 {b['hours']}/{a['hours']}h 상점 정산 {b['paid']}→{a['paid']}, 구매 지급 {b['buyer_paid']}→{a['buyer_paid']}, 시장 정산 {b['market_paid']}→{a['market_paid']}. 숙성 {b['mature']}→{a['mature']}, 확인원가 순기여 {b['mature_net']}→{a['mature_net']}원(원가누락 {b['missing_cost']}→{a['missing_cost']}). {AUDIT}:{i}. "
 grade='unclear'
 missing='동시 변경·시장 수요와 이 변경의 효과를 분리한 비교군'
 measurement='이번 전후 원장 정산 및 주문연결 환불 재집계'
 judge='11/07 23:37 KST, 동일 SKU·등급 숙성 40건의 기존 정책 대비 순기여/시간 >0 및 Players 비악화일 때 확대; 미달·표본부족이면 확대 중단, 사람의 보호 변경은 유지'
 if any(x in title for x in ['chore(', 'Merge branch']):
  grade='no_effect';reason+='기록·병합 자체의 독립 거래효과를 주장하지 않는다. 구현은 각 원본 커밋에서 별도 평가.'
 elif title.startswith('revert(') or title.startswith('wip('):
  grade='no_effect';reason+='검증 거절과 원복으로 최종 적용되지 않은 시도다. 실패 증거를 보존한다.'
 elif sha=='6362f66':
  grade='no_effect';reason+='G6 cap 343000→350000은 관측 ask 303125/307655/305808보다 높아 직접 가격효과가 없다(측정 파일:3). 가격 문턱 구속을 확인하지 않은 Terra 판단은 감점.'
 elif sha=='636d35e':
  grade='no_effect';reason+='SKU 집계를 추가했으나 24284자 응답이 8000자 전송 제한에 잘려 재고불명 판단이 지속됐다. 실제 의도 달성 실패이며 6b3710b의 전송 수정으로 대체한다.'
 elif sha in ['3f3a7ee','c7df36c','8e48bc2']:
  grade='harmful';reason+='특정 표본의 사후 비교에서 기준 미달로 사람이 이미 원복/비활성화했다. 현재 원복 상태 유지. 합산 전후 수치만으로 개선이라 판단하지 않는다.'
 else:
  if 'buyer' in title.lower() or 'oneshot' in title.lower():
   judge='11/07 23:37 KST, 동종 100주문에서 지급률과 Saved/주문이 대조보다 낮지 않고 잘못된 영수증 지급 0건; 미달이면 해당 신규 경로만 이전 동작으로 복구 검토(사람 고정 스위치 유지)'
  if sha=='6b3710b':
   missing='수정 뒤 실제 재입고 판단 3건';judge='11/07 14:00 KST, 3판단 중 재고불명 축소 0건이면 유지, 아니면 전송/소비 경로 수정 또는 상세 5개 제한만 복원'
  reason+=f'결측: {missing}. 측정: {measurement}. 판정: {judge}.'
 grades.append({'change':sha+' '+title[:100],'grade':grade,'reason':reason})
 # Complete file coverage, including owner changes. No wholesale reversal of overlapping commits.
 keep.append({'item':sha+' '+', '.join(files),'decision':'keep','reason':'현재 HEAD의 적용·원복 상태를 유지한다. '+('기각/중단된 옛 구현을 다시 켜지 않는다.' if grade in ['harmful','no_effect'] else '인과효과 미확정으로 추가 확대를 보류하며 위 판정선으로 검토한다.')})
 for label in ['before','after']:
  fragment=json.dumps(r[label],ensure_ascii=False)
  cite(i,'"'+label+'": '+fragment,sha+' 전후 정산·숙성 순기여; 원본 ledger 경로는 sources에 보존')
# Every Terra strategy check and patch/scale judgment since the prior review.
for i,r,line in rows:
 if r['kind']!='cycle':continue
 cycle=r['cycle'];state=r['status'];g='unclear'
 reason='Patch 3.5 이후 원값 포럼/Players를 사용하고 호출 상한을 되살리지 않았다. 현행 유지의 경쟁자 성장 비교는 타당하지만 개별 추가 ΔPlayers와 SKU 세 조건 검증은 부족했다.'
 if cycle.endswith('232552'):g='harmful';reason='surge 경보를 답하지 않아 no_change_unsupported. 경보를 설명하지 않은 검토 실패.'
 elif cycle.endswith(('030056','074938')):g='harmful';reason+=' 계산 가능한 두 창 잔류를 반복 미확인으로 남겨 감점. 총상한 비구속과 SKU target 구속은 별개다.'
 elif cycle.endswith('063708'):g='no_effect';reason+=' cap이 ask를 구속하지 않아 수치 복원 효과0. 당시 전후 판매158→111로 원인 효과를 주장할 수 없다.'
 elif state=='rejected':g='harmful';reason+=' 검증/인용 실패로 전달되지 않은 결과다. 테스트 통과와 인용 유효성을 분리해야 한다.'
 elif cycle.endswith('112418'):reason+=' 전송 제한을 발견하고 수정한 점은 유효. 실제 3판단의 결과는 14:00까지 미성숙.'
 reason+=f' 근거 {r["ref"]}; 결측은 독립 효과, 이번 원장 전후 감사로 측정. 23:37까지 근거 완비 판단2/3 이상, 미달이면 새 맥락 지침 철회. 두 창 stock cost 절단0/0이라 총상한 미인상 자체는 감점하지 않는다.'
 grades.append({'change':cycle+' 전략·규칙·확대 점검','grade':g,'reason':reason})
 cite(i,'"cycle": '+json.dumps(cycle),reason)
# Include every discovered tool and every historical generated tool in pack usage.
p=json.load(open('meta/evidence/astra-1107-113730.json'))
toolrows={r['name']:(i,r) for i,r,_ in rows if r['kind']=='tool'}
for name in sorted(set(toolrows)|set(p['tools'])|{'supply_audit'}):
 calls=p['tools'].get(name,{});live=Path('tools',name,'tool.py').exists();bad=name in ['auto_market_research_bundle','auto_auto_market_research_forum']
 reason=f"누계 호출 {calls.get('calls',0)}, 실패 {calls.get('failed',0)}. "
 if name in toolrows:
  i,r=toolrows[name];reason+=f"이번 구간 {r['calls']}호출/{r['failed']}실패({AUDIT}:{i}). "
 if bad:reason+='실제 settle 1건 fixture에서 0건 반환. 도구·manifest를 이번에 삭제, 전용 테스트는 없었으며 원본은 dropped-tools.json에 보존.'
 elif not live:reason+='이미 없는 도구의 삭제 상태 유지; 재등록하지 않는다.'
 elif name=='supply_audit':reason+='새 읽기 전용 도구. 실제 sandbox 실행과 시간·ID·비판매 출고 회귀검증 통과. 3판단의 활용성은 미확정.'
 elif name in ['auto_auto_market_brief','auto_market_scan']:reason+='SKU 보존·settle 중복제거 구현을 유지. 이번 사용0이라 추가 생성은 중단, 다음12h 사용·유효 인용1회 미달이면 통합 검토.'
 else:reason+='실패율만으로 의미 정확성을 보장하지 않으며 기존 테스트와 실제 응답 검증을 유지.'
 keep.append({'item':'tools/'+name,'decision':'drop' if bad else 'keep','reason':reason})
for name in ['auto_market_research_bundle','auto_auto_market_research_forum']:
 grades.append({'change':name,'grade':'harmful','reason':'실행 실패0이어도 결제1건 fixture를 0건으로 반환. 상품명→SKU→settle 연결 실패를 재현하여 제거했다.'})
# Essential direct measurements and successful tests.
for path,n,excerpt,meaning in [
 ('meta/evidence/astra-1107-113730-probes.jsonl',1,'"expected_paid": 1','삭제 도구가 실제 결제행을 놓치는 의미검증'),
 ('meta/evidence/astra-1107-113730-probes.jsonl',2,'"deal_count": 0','두 번째 삭제 도구도 같은 실패'),
 ('meta/evidence/astra-1107-113730-probes.jsonl',6,'"sold_fraction": 1.0','G24 공급창 판매1/1; 규모 확대의 나머지 조건과는 구별'),
 ('meta/evidence/astra-1107-113730-probes.jsonl',7,'"objective_matches": true','실제 /rules와 점수표 일치'),
 ('meta/evidence/astra-1107-113730-measurements.jsonl',1,'"stock_cost_cuts": 0','첫 완료창 총상한 비구속'),
 ('meta/evidence/astra-1107-113730-measurements.jsonl',2,'"stock_cost_cuts": 0','두 번째 완료창 총상한 비구속')]:cite(n,excerpt,meaning,path)
for n,l in enumerate(open('meta/evidence/astra-1107-113730-tests.txt'),1):
 if l.startswith('Ran '):cite(n,l.strip(),'지정 환경 전체 테스트 통과','meta/evidence/astra-1107-113730-tests.txt')
opts=[{'direction':'현행 폭넓은 상품·고가 회전 유지와 공급창 근거 개선','expected_profit':'기준 정책 대비 추가 ΔPlayers=0. 3.8h/12.2h 속도 연장 시 우리 +.07263/+.09738, 선두 −.02526/−.02459; 12h 후 격차 .03411/.01003. 새 계측의 직접 이익은 0.','risk':'두 창 실측이 있어도 실제 target 구속·숙성 이익이 없으면 확대 불가.','test':'supply_audit 맥락을 재입고 3판단에 적용; 근거 완비2건 이상과 재고불명 축소0건.'},
{'direction':'고가 상품 일괄 확대 또는 가격 재인하','expected_profit':'추가 book 27.97M 및 매출150.27M의 다음 문턱을 둘 다 넘을 때만 +.01403; 경쟁자 이동은 위 두 속도 적용. 기대값 미식별이라 선택 안 함.','risk':'가격시험 판매/보유 .33 대 .44; 환불·서지·보관비와 현금완충.','test':'총상한 절단0/0이므로 총상한 인상 대신 SKU별 세 조건부터 확인.'},
{'direction':'구매 Saved 및 미충족 포럼 수요 연결','expected_profit':'Saved .2667→.2718과 forum .882→.885가 현행 대비 추가 개선이면 +.01016. 기대값 미식별; 다음 무료 탐색만 선택.','risk':'수요글은 실제 지급이 아니며 식품 유통기한·예산 부적합 가능. 유료 비추천은 book 비용.','test':'#30979/#30992 포함 수요3개에서 예산·유통기한·settle을 갖춘 후보1개; 메시지·매입 없음.'}]
report={'changed':True,'hypothesis':'실패 없이 빈 결제 결과를 주는 도구와 오래된 검토 기준을 제거하고 공급창 단위 ID를 제공하면, Terra의 반복 미확인과 근거 없는 규모 판단을 줄일 수 있다.','change_summary':'전략·Terra 지침 갱신, 잘못된 자동도구2개 삭제, supply_audit 및 회귀테스트 추가, 팩 관측시각·직전누락·음수 차분 처리 수정, 직렬 실험2개 등록. 수익 설정과 보호한도는 유지.','target_metric':'Players 순위; 보조 지표는 근거 완비 재입고 판단 수와 잘못된 결제 검색0건','expected':'12h 추가 Players 수익은 아직 미식별. 재입고3판단 중 근거완비>=2, 재고불명 축소0건; 실제 settle fixture 누락2개 제거.','evidence':evidence,'evidence_gap':{'missing':'신규 맥락이 재입고 행동·추가 Players에 미치는 독립 효과와 실제 target 구속','measurement':'전후 정산108커밋, 원가/환불 연결, 두 공급창 guard0/0 및 unit ID 감사, 도구 의미 fixture 실행. G24 1입고1판매, G03 1입고0판매.','judge_line':{'metric':'근거 완비 재입고 판단 수; 재고불명 축소 건수','direction':'above','threshold':'완비>=2 및 재고불명 축소=0','min_sample':'재입고3판단, 완료 공급창2개','expires':'2026-11-07 23:37 KST; 기존 my_state는14:00 우선 판정','undo':'신규 restock 맥락 문장만 제거. 도구 오류면 supply_audit와 테스트 제거. 보호한도·총량은 유지.'}},'summary':'Players 1.572, 10/61. 108커밋과 11 Terra 점검을 전수 검토했고 762테스트가 통과했다. 동시 변경·미숙성 때문에 인과효과는 다수 미확정이다. 상세 판정은 이 파일의 grades/keep_or_drop과 감사 원장에 보존. 커밋·push·PR 없음.','strategy':{'options':opts,'chosen':'현행 전략 유지. 자료가 현행 대비 우월한 수익 대안을 입증하지 못해 공급창 검증을 먼저 하고 미충족 수요 연결을 작은 두 번째 실험으로 둔다.','experiment':'supply-audit-context-113730 다음 forum-demand-join-113730; Terra가 하나씩 시작. 각각12h, 지표·표본·철회조건을 큐에 기록.'},'grades':grades,'keep_or_drop':keep}
Path('meta/evidence/astra-1107-113730-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=1)+'\n')
# Capture exact setting/file diffs per commit so no setting is hidden by a title summary.
with open('meta/evidence/astra-1107-113730-settings.jsonl','w') as f:
 for i,r,_ in rows:
  if r['kind']!='commit':continue
  diff=subprocess.check_output(['git','show',r['commit'],'--format=','--','pricing.json'],text=True)
  f.write(json.dumps({'commit':r['commit'],'files':r['files'],'pricing_diff':diff,'decision':'현재 HEAD 유지; 이미 원복한 값 재도입 금지'},ensure_ascii=False)+'\n')
print(json.dumps({'commits':sum(r['kind']=='commit' for _,r,_ in rows),'grades':len(grades),'decisions':len(keep),'evidence':len(evidence)},ensure_ascii=False))
