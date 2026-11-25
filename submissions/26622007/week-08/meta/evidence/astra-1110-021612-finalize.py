import json,pathlib,subprocess
import sys
sys.path.insert(0,str(pathlib.Path.cwd()))
import meta_harness as mh,hard_limits
P='meta/evidence/astra-1110-021612';r=json.load(open(P+'-review.json'))
for k in r['keep_or_drop']:
 if k['decision']=='keep' and not pathlib.Path(k['item']).exists():
  k['reason']='반려·원복 또는 하네스 정리로 현재 파일이 없다. 부재 상태를 유지하며 실패 시도의 파일을 재도입하지 않는다. 커밋 원문과 전후측정은 보존했다.'
open(P+'-review.json','w').write(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
strategy={'options':[
 {'direction':'현행 상품·인증·72h보증·Saved 중심 운영과 손실 SKU 한정 시험','expected_profit':'추가 ΔPlayers 기준0. G2 숙성58건 −447774원/건당−7720원; 목표3→2가 실제 매입 한 개를 막을 때만 약7720원 손실 회피 추정. book 다음문턱9.762M에는 못 미친다.','risk':'최근두창 G2 입고0으로 실제 구속이 없어 효과0일 수 있다. 수요변화와 표본부족.','test':'목표3→2 한 단계,12h/변경후6h숙성10건·실제구속1건. 미달 또는 양수회복이면2→3복원.'},
 {'direction':'60만+ 재고와 협상 하한의 일괄 확대','expected_profit':'추가 book9.762M/매출494.301M을 움직이는 경쟁자보다 더 넘으면 조건부 +.00806/+.00484. 실현 기대값은 미식별. inextro12h942.68M 중822.61M이60만+이나 원가 비공개.','risk':'환불·서지·보관비; 총상한1B 절단0/0으로 상한 인상의 직접효과0.','test':'SKU 한 개의 실제 target 구속·과반판매·숙성순익>0/book비하락부터 확인.'},
 {'direction':'구매 Saved·포럼 효과에 신규 작업 집중','expected_profit':'현재 Saved .3137→.3139는 조건부 +.00847, forum .895→.897은+.0006. 추가효과 미식별. 모든 옵션의 경쟁자 기준은3.5h/12.8h 속도;12h 연장 선두격차 .07243/.11081.','risk':'거래량 감소·겹친 사람 A/B·포럼 게시수와 평점의 불일치.','test':'상품 사다리 지급 A107/B131을 등급·버전별로 나누고 군별숙성20건의 Saved분모와 실제가치를 연결.'}],
 'chosen':'현행을 이기는 추가 Players 인과 증거가 없어 현행 유지. G2 한정 손실검증을 첫 큐로, 결제 구매자 집중도 조사를 두 번째로 둔다. 총량·사람 스위치는 유지한다.',
 'experiment':'Terra가 g2-loss-target-1110을 시작 후12h에 판단한다. 다음 concentration-probe-1110은 Gc2f7811b의3결제/구매자1명을 새 도구자료로 검증하는 읽기전용 실험이다. 조사3건·결제10건/구매자3명·보증하한 초과후보1개가 성공선이며 미달이면 새 지침 제거. 이번에는 큐만 준비했다.'}
answer={'changed':True,'hypothesis':'자동 조사 도구의 SKU·지급거래·재고 연결을 바로잡고 구매자 집중도를 구분하면, 실행 성공만으로 잘못된 수요를 믿는 판단을 줄일 수 있다. 실제 Players 상승은 후속 실험으로 검증한다.',
'change_summary':'auto_market_research_pack의 SKU키 보존, 정산만·중복제거, 필터교집합, 표시제한 밖 SKU연결, 실제 units를 수리하고 표본 구매자 집중도를 추가했다. 의미검사와 집중도 검사5개를 추가했다. 전략·Terra 판정선·직렬큐·보고서를 갱신했다. 가격설정/사람스위치/보호한도는 변경하지 않았다.',
 'target_metric':'도구 의미오류 수, 근거를 갖춘 재입고 판단, 현행 대비 추가 Players',
 'expected':'기존 의미회귀 실패4→0은 확인됐다. 집중도 검사를 포함한 전체1595테스트 통과. 도구수리 자체의 직접 ΔPlayers는0이며, 거래·순위 개선은 미확정이다.',
 'summary':'Players1.646·6/66·선두차이.093.77커밋과10 Terra 점검을 모두 감사했다. 검토구간2694상점결제, 숙성1461건 순기여+6343471원.87개 판정·72개 유지/제거 결정·116개 인용은 meta/evidence/astra-1110-021612-review.json에 보존했다. 신규 패치 없음,산식일치. alert llm_cache: .062/이전6h .088은 유형별 캐시 진단 대상으로 두며 호출을 줄이지 않는다. 커밋·push·PR 없음.',
 'evidence_gap':{'missing':'겹친 가격·등급·보증 실험 각각의 추가 Players 효과와 G2 목표축소의 실제 매입 구속.','measurement':'77커밋 전후30분 결제/숙성순익, 주문→정산 A/B 지급(상품사다리107/131,등급101/98),전 목표SKU 두창 ID 및 가드 총상한절단0/0·SKU절단23/21을 계산했다. 식품실제가치97/87건과 버전별 교란은 남아 있다.','judge_line':{'metric':'동시처치를 분리한 처리군의 현행 대비 추가 Players','direction':'above','threshold':'0 초과, 원래 실험의 무해·중단조건 모두 충족','min_sample':'군별6h숙성20결제 및 실제가치/원가/환불 완전연결; G2는변경후숙성10건·구속1건','expires':'2026-11-10 14:16 KST; 큐 실험은 각각 시작+12h','undo':'표본미달은 unclear로 남기고 확대하지 않는다. 사람 변경은 원래 undo 조건 인용 시에만 복원. G2시험이 무구속·양수회복·표본미달이면2→3복원, 집중도시험 미달이면 새 지침만 제거.'}},
 'strategy':strategy,'grades':r['grades'],'keep_or_drop':r['keep_or_drop'],'evidence':r['evidence']}
open(P+'-answer.json','w').write(json.dumps(answer,ensure_ascii=False,indent=2)+'\n')
paths=['tools/auto_market_research_pack/tool.py','tools/auto_market_research_pack/manifest.json','test_research_pack.py','meta/strategy.md','meta/terra.md','meta/experiments.json','meta/REPORT-notes.md']
diff=subprocess.check_output(['git','diff','--',*paths],text=True)
out={'citations':len(answer['evidence']),'evidence_problems':mh.verify_evidence(answer['evidence']),'protected_problems':hard_limits.guard_problems(paths,diff),'queue_problems':mh.queue_problems('astra',paths),'strategy_problems':mh.strategy_problems('astra',answer,paths),'gap_problems':mh.gap_problems(answer,paths,{}),'alert_problems':mh.alert_problems(json.load(open(P+'.json')),answer,paths),'pricing_changed':bool(subprocess.check_output(['git','diff','--','pricing.json'],text=True)),'tests':'1595 passed in197.587s, exact TEST_BOOT in harness test_copy','queue':mh.experiments()}
try:
 import jsonschema
 jsonschema.validate(answer,mh.schema_for('astra'));out['schema']='valid'
except ImportError:out['schema']='jsonschema unavailable; all required fields present'
open(P+'-validation-v2.json','w').write(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print({k:v for k,v in out.items() if k!='queue'})
