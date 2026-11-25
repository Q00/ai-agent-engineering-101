"""Assemble this cycle's line-cited review; no trading, git writes or log edits."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
AUDIT='meta/evidence/astra-1104-090509-audit.jsonl'
rows=[(i,json.loads(l)) for i,l in enumerate((ROOT/AUDIT).read_text().splitlines(),1)]
result={'changed':True,
'hypothesis':'G75f11c19 목표를 1→2로 올리면 검증된 침구의 공급창 사이 판매 기회가 늘어난다. 숙성 손익을 분리하면 미숙성 매트리스 수익을 근거로 과도하게 확대하는 판단을 줄일 수 있다.',
'change_summary':'G75f11c19 목표 2와 관련 테스트, 읽기 전용 cohort_profit 도구·테스트 및 scorecard의 숙성 손익을 추가했다. 실제 데이터에서 빈 검색과 미결제 result 행을 반환한 auto_catalog_ledger_search를 제거하고 원본을 증거로 보존했다. 전략과 첫 전수 평가·보고 노트를 작성했다.',
 'target_metric':'12시간 G75 유료 판매·숙성 순기여, 현금 순이익 및 Players, 두 Terra 사이클의 숙성 손익 인용과 도구 오류',
 'expected':'G75 판매 14건 기준 대비 4건 추가(총 18건 이상), 추가 기여 약 87,395. 최악 추가 재고 원가 92,175. 다음 두 Terra 평가에서 숙성/미숙성·누락을 구분하고 도구 오류 0건. 순위 상승은 미확정.',
 'evidence':[],
 'evidence_gap':{'missing':'G1515의 6시간 숙성 표본과 환불 연결 정확도, 동시 변경을 분리한 인과 효과가 부족하다. 경쟁점의 원가는 공개되지 않는다.',
 'measurement':'이번 사이클에 모든 커밋 전후 최대 30분의 kit 유료 판매·원가·판매 seq 환불을 집계하고 시장 거래수를 함께 비교했다. 숙성 13건 중 원가 누락 1건을 kit 14176행의 300,207로 수동 보완하면 순기여 -31,314다. cohort_profit을 다음 점수표에 추가했다.',
 'judge_line':'15:05 KST 이후 09:05까지 판매를 재집계한다. 숙성 ≥20건·환불 비율 >15% 또는 최근 숙성 10판매 순기여 <0이면 G1515 목표 4→3. 원가 누락은 먼저 unit PRICE 로그로 보완. G75는 21:05에 ≥18판매·첫6시간 코호트 순기여 >0이면 유지, 추가 단위가 2창 연속 미판매거나 숙성 시험 기여 <0이면 목표1 복원.'},
 'summary':'09:05 팩의 Players 1.325, 순이익 6위(9,406,642), 선두 24,462,835. 14개 지정 커밋을 포함해 첫 기록 이후 48개 커밋과 Terra 13개 평가/전략 점검을 검토했다. 현 전략을 유지하고 침구 한 단위 시험과 숙성 손익 측정을 시작했다. 290개 테스트 통과. 커밋·push·PR·메시지 전송 없음.',
 'strategy':{'options':[
 {'direction':'현재의 폭넓은 이익 재고 + 검증 매트리스, 침구 한 단위 시험','expected_profit':'12시간 현금 순이익 +1,000만~1,800만, 시험 +0~87,395. 07:01→09:05 현금 718만→940만, 원가회수 가정 940만→1,223만의 시간당 증가율을 단순 환산한 시나리오다.','risk':'경쟁 수요 변화와 미숙성 환불. 추가 침구 원가 최악 92,175; 재고 400만·현금450만 한도 안에서 한 단위만 시험.','test':'G75 목표1→2, 다음12시간 판매18건 이상·첫6시간 숙성 순기여 양수, 2창 연속 잔류면1로 복원.'},
 {'direction':'검증 매트리스에 더 집중','expected_profit':'기존 시나리오 +0~45만: G6 숙성12판매 순기여900,562, 건당75,047 × 추가0~6판매 가정.','risk':'G6 보충 재개 후 아직1시간 미만. G1515 숙성13건 원가보완 순기여-31,314이며 소표본; 집중은 보증·묶인 자금 위험을 키운다.','test':'현재 G6 목표2를 먼저 한 시간 및 보증6시간 관찰. 연속2창 매진과 숙성 양수 뒤에만 추가1 검토.'},
 {'direction':'선두의 다른 고가 가구와 미충족 소파 수요 진입','expected_profit':'기존 시나리오 +0~50만은 0~10판매×가정5만 마진의 검증 전 범위. dae-yangkim G5cd4ffbc 90판매·매출14,547,255이나 원가는 미공개다.','risk':'구합니다 소파 예산168만~534만은 현행 한도와 자금 부담이 크고, 주문 전환·원가·보증 손익이 없다. 실패 시 매입 원가만큼 손실.','test':'먼저 공개 원장 구매자≥3명·시간당≥10거래·우리 보증하한 충족 여부를 두 사이클 비교; 지금 자동매입하지 않는다.'}],
 'chosen':'현재 전략 유지. 경쟁점 확대 전략이 더 낫다는 자체 마진 자료가 없다. 카드·인증·6시간 보증·협상 규칙을 유지하고, 회전이 검증된 침구 한 단위로 먼저 시험한다.',
 'experiment':'주 실험 G75 목표1→2: 21:05 KST에 유료18건 이상, 첫6시간 판매의 숙성 순기여 양수, 2창 연속 미판매 없음이면 유지. 실패하면1로 복원. 작은 실험은 cohort_profit 측정: 두 Terra 사이클에서 숙성/미숙성·원가누락 구분, 오류0; 15:05에 G1515 재평가. 마지막6시간 판매 최종 보증 판정은11/05 03:05.'},
 'grades':[], 'keep_or_drop':[]}

def evidence(ref, excerpt, meaning):
    line=int(ref.rsplit(':',1)[1]);path=ref.rsplit(':',1)[0]
    actual=(ROOT/path).read_text().splitlines()[line-1]
    assert excerpt in actual,(ref,excerpt)
    result['evidence'].append(dict(ref=ref,excerpt=excerpt,meaning=meaning))

worked={'e7204c8','a7ca54a','42f6fcc','605acc7','d610718','58d091d','6f76497','0f8a56f','aad de40'.replace(' ',''),'2fb6eeb'}
noeffect={'e3d7228','1eed138','061dca4','dad4e8b','9ec5da6','2ad994e','5a8d075','1b791f1','773dedf'}
notes={
'7560b38':'폭 확대·영수증 품목명 수정은 함께 배포돼 기여 분리가 안 된다.',
'a2fe5de':'guard 후 줄 자르기·공백 제거는 동시 변경이고 실제 절단 사유 로그는 아직 없었다.',
'e7204c8':'공식 보충 경로 이후 유료량 증가를 관측했다. 시장 증가와 앞선 폭 확대도 기여할 수 있다.',
'f3942f1':'전후 guard 절단 로그 0→6,062행은 관측 기능 추가의 효과이며 고유 주문 손실 6,062건을 뜻하지 않는다. 판매량 개선은 미확정.',
'5a8d075':'문서·오류 사유 개선으로 경제 효과를 분리할 수 없으며 런타임 확대 실험이 아니다.',
'a7ca54a':'판매·관측 순기여는 예측 방향이나 매입량과 최초가를 함께 바꿔 각각의 효과는 분리하지 못한다.',
'42f6fcc':'배치 우선순위 수정 뒤 판매 증가, 반복 절단 로그 5,996→5,397. 공급창 변동도 설명 가능하다.',
'025165a':'느린 세 번째 단위 축소는 원래 되돌림 규칙을 따랐다. 전체 판매 증가는 해당 SKU의 회전 효과를 뜻하지 않는다.',
'db8eee0':'plan 단계 우선순위 수정 후 짧은 구간 판매·기여가 줄어 단독 이익 개선을 입증하지 못한다.',
'b996e14':'목록 밖 고가 차단·느린 SKU 제외·목표·현금 버퍼를 함께 바꿨다. 짧은 구간 판매 감소는 수요/공급창과 혼재한다.',
'1b791f1':'보유+대기+신규 합계 상한을 명시한 주석이며 경제 동작은 같다.',
'b3324d9':'50초 경계 수정 뒤 판매 증가가 보이나 공급 거절은 다른 창 마감·품절도 포함하므로 조기 전송 유실만의 감소는 미확정.',
'605acc7':'호가 상한 뒤 G6의 보유2개가06:34/06:36 결제되어 막힌 재고 해소를 관측. 동시 공급창 변경의 영향도 있다.',
'773dedf':'NaN·무한대 방어는 테스트로 검증하는 오류 예방이며 실거래에서 해당 입력 노출 증거는 없다.',
'd610718':'구매자 결제6→25건, 사전 유통기한·다음 견적 묶음·지출 상한 변경이 한 묶음이라 각 효과는 분리하지 못한다.',
'c8a63b1':'구매자 결제7→27건이지만 직전 수정과2분 차이여서100초 제한의 개별 이익은 미확정.',
'58d091d':'실기억 오염 환경 제거 후 전체290테스트 통과. 판매 증가 자체는 서비스 복구·다른 수정과 혼재한다.',
'6f76497':'SHOP_MEMORY·AGENT_MEMORY_DIR 격리를 유지하며290테스트 통과. 별도 경제 효과 주장은 하지 않는다.',
'0f8a56f':'숫자 제안 fallback 0→183회로 목표5회를 넘었다. 유료0→88건은 재시작/호출한도 회복도 포함하므로 전부 이 수정의 추가 판매가 아니다.',
'2fb6eeb':'첫 Astra가09:05 실행됐고 부족한 증거의 측정/판정선 검사 테스트 통과. 경제효과와 실행 복구를 구분한다.',
'aadde40':'이번 curl 규칙 읽기 성공. 규칙 접근 복구는 관측됐지만 직접 이익 효과는 분리하지 않는다.',
'6b17b38':'보충 보류 해제 뒤 G6가08:50에2개 유료 판매되어 목표 판매량은 도달; 한시간·6시간 보증이 지나지 않아 순이익 판정은 유보한다.'}
for i,d in rows:
    if d['kind']!='commit':continue
    sha=d['commit'];b=d['before'];a=d['after'];ref=f'{AUDIT}:{i}'
    title=d['title'];grade='worked' if sha in worked else 'no_effect' if sha in noeffect or title.startswith('chore') else 'unclear'
    reason=f"전후 각 최대30분 유료 {b['paid']}→{a['paid']}건, 원가확인분 환불후 기여 {b['known_net']}→{a['known_net']} ({ref}). "
    reason+=notes.get(sha,'배포·재시작·시장 수요와 겹친 구간이며 기록 전용/거부·복원 커밋은 별도 경제 처치가 아니다.' if grade=='no_effect' else '여러 운영 변경이 같은 구간에 겹쳤다.')
    if grade=='unclear':reason+=' 부족: 단독 효과/숙성 노출. 이번 전후 kit·시장 조회로 측정; 다음12시간 두 동기간 구간의 공급창당 판매와 숙성 순기여가 모두 증가하면 지지, 순기여 음수면 해당 되돌림 조건부터 적용.'
    result['grades'].append(dict(change=sha,grade=grade,reason=reason))
    evidence(ref, '"before": '+json.dumps(b,ensure_ascii=False), f'{sha} 적용 전 측정; samples에 실제 kit 경계행 포함')
    evidence(ref, '"after": '+json.dumps(a,ensure_ascii=False), f'{sha} 적용 후 측정; 원가 누락·시장 전체 거래수도 함께 제공')
    decision='keep'
    result['keep_or_drop'].append(dict(item=sha+'의 현재 남은 코드·프롬프트·설정·기록',decision=decision,
        reason='실패/되돌림/기록 이력은 보존하고 거부된 설정은 재적용하지 않는다.' if grade=='no_effect' else '현재 후속 설정을 유지한다. '+notes.get(sha,'단독 유해 효과가 확인되지 않았으므로 원래 되돌림 조건을 적용한다.')))
for i,d in rows:
    if d['kind']=='terra':
        c=d['cycle'];status=d['status'];grade='no_effect' if status=='rejected' else 'worked' if c.endswith('072946') else 'unclear'
        reason={'terra-1103-225335':'watch는 고가 경쟁점 관측에 부합하나 원가·전환율 측정이 빠졌다.',
                'terra-1103-235434':'거래15건 증가로 정체 조건 미충족이므로 keep은 규칙에 맞는다.',
                'terra-1104-005515':'품절58건을 SKU·guard 원인으로 나누지 않고 대기했다.',
                'terra-1104-015602':'체결70건 증가를 기록했으나 반복 품절·재고 회전 측정을 남기지 않았다.',
                'terra-1104-025643':'확대와 파손 선후는 git 시각·판매seq로 이번에도 조회할 수 있었다. 증거 부족을 해소하지 않은 판단이었다.',
                'terra-1104-035718':'축소는 테스트 환경 오류로 거부됨. 현재 1시간 경과47판매·5환불은15% 미만이며 당시 두파손만으로 현규칙을 소급 적용하지 않는다.',
                'terra-1104-045914':'2시간 since_last_cycle을 구분한 점은 맞지만 최신 사람 전략을 반영한 SKU별 검증이 부족했다.',
                'terra-1104-055948':'HEAD의5개 실패를 확인했으나 실기억 환경원인을 해결하지 못해 행동을 미뤘다.',
                'terra-1104-070114':'시스템 python의 httpx 누락을 경제적 변경 보류 근거로 삼았다. 지정venv로290개 통과를 이번에 확인했다.',
                'terra-1104-072946':'버그 진단·keep과 fallback 유지가 타당: 전후30분0→183회. 추가 유료 판매의 인과성은 미확정.',
                'terra-1104-084118':'G6 보유2개 판매 후 보류해제는 조건에 맞음. 재개후2판매이나6시간 미숙성. fallback635회를162판매 전체의 원인으로 주장하지 않은 점은 타당.'}.get(c,'거부되어 시장에 지속 적용되지 않았으며 검증 실패 이력을 보존한다.')
        reason+=f' meta/grades.jsonl:{i-60} 대신 원본 위치는 {d["ref"]}; 이번 전수 조회는 {AUDIT}:{i}.' if False else f' 근거 {d["ref"]}, 조회 {AUDIT}:{i}.'
        if grade=='unclear':reason+=' 부족: 실행/전략의 반사실. 이번 코호트 조회를 측정으로 남김; 다음 두 사이클에 숙성·미숙성·누락과 판정선을 모두 인용하면 절차 개선, 없으면 실패.'
        result['grades'].append(dict(change=c+' 평가·전략 점검',grade=grade,reason=reason))
    if d['kind']=='tool':
        name=d['name'];decision='drop' if name=='auto_catalog_ledger_search' else 'keep'
        reason=f"기록 호출 {d['calls']}회·실패 {d['failures']}회. "
        reason+=('실제 SKU 검색이 빈 값, 무필터 검색은 result행까지 반환. 이번 사이클 도구와 manifest selftest를 삭제했고 별도 테스트 파일은 없었다.' if decision=='drop' else
                '03:59 실패 커밋 복원에서 이미 삭제된 상태를 유지한다;100회 오류0은 유용성 증거가 아니다.' if name=='auto_market_lookup' else
                '등록을 유지한다. 호출0이면 효과 미검증이며 다음12시간 호출/오류/결정 인용으로 평가한다.' if d['calls']==0 else '현재 읽기 전용 기능과 sandbox를 유지한다; 오류0만으로 이익 증가를 주장하지 않는다.')
        result['keep_or_drop'].append(dict(item='tools/'+name,decision=decision,reason=reason))
result['grades'].append(dict(change='tool_forge auto_catalog_ledger_search',grade='harmful',reason='정상 SKU도 빈 검색, 미결제 result를 deals로 반환하는 의미 오류를 sandbox에서 재현했다. 실거래 호출0으로 손익 피해는 관측되지 않았다. logs/astra-1104-090509-tool-audit.jsonl:1-2. 삭제했다.'))
result['grades'].append(dict(change='tool_forge auto_market_lookup',grade='unclear',reason='100호출·실패0 뒤03:59 복원에서 삭제. 빈 검색을 반복한 kit41015행이 있지만 순이익 효과는 분리 불가. 이번 도구·호출 집계로 측정했고 대체 검색도구는 다음12시간 실제 일치SKU 반환과 결정 인용≥1건을 충족해야 유용성을 인정한다.'))
result['keep_or_drop'].append(dict(item='현행 전략·숫자 fallback·G6 목표2·가격상한·G1515 목표4·총재고400만·현금450만·고가목록·느린SKU 제외·50초 공급창',decision='keep',reason='원래 되돌림 조건을 충족한 G6 보류해제를 유지한다. 이번 G75 목표만1→2. G1515의 숙성 위험은15:05 재판정하며 보증과 파산 guard를 유지한다.'))
result['keep_or_drop'].append(dict(item='cohort_profit 및 scorecard 숙성 손익',decision='keep',reason='유료 결제만 연결하고 누락은null로 보고한다. 3개 새 테스트 및 전체290개 통과; 두 Terra 사이클의 사용성과 오류0을 판정선으로 둔다.'))
# Small, direct raw citations support the economic experiment and tool removal.
for ref,excerpt,meaning in [
 ('logs/market-kit.log:163486','guard: G75f11c19 4 -> 1 (units per SKU)','공식 수량을 목표1이 제한했다.'),
 ('logs/market-kit.log:163813','1 x 프리미엄 앤 모달 차렵 이불패드세트 Q for 54030','입고 원가와 시각'),
 ('logs/market-kit.log:163908','at 82845, ledger #2317472','27초 뒤 유료 결제'),
 ('logs/market-kit.log:14176','unit u-85a7899bd11d cost 300207','누락된 숙성 판매의 원가를 이번에 보완'),
 ('logs/market-kit.log:14284','at 381909, ledger #2175180','누락 기억을 유료 ledger와 연결'),
 ('logs/astra-1104-090509-tool-audit.jsonl:1','"result": {"catalog": [], "deals": []}','실제 SKU 검색 실패 재현'),
 ('logs/astra-1104-090509-tool-audit.jsonl:2','"k": "result"','deals에 미결제 result행이 혼입'),
 ('logs/astra-1104-090509-tool-audit.jsonl:4','"catalog_has_sku": true','실제 카탈로그에 해당 SKU가 존재'),
 ]:evidence(ref,excerpt,meaning)
for i,d in rows:
 if d['kind'] in ('cohort','rival','tool','forum','terra'):
  line=(ROOT/AUDIT).read_text().splitlines()[i-1]
  evidence(f'{AUDIT}:{i}',line,'이번 사이클의 원장/기억/도구/전략 원문 조회 결과')
log=(ROOT/'logs/astra-1104-090509-tests.log').read_text().splitlines()
for i,l in enumerate(log,1):
 if l.startswith('Ran 290 tests'):evidence(f'logs/astra-1104-090509-tests.log:{i}',l,'지정 venv 전체 테스트 결과')
(ROOT/'meta/evidence/astra-1104-090509-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print('grades',len(result['grades']),'keep_or_drop',len(result['keep_or_drop']),'evidence',len(result['evidence']))

if __name__=='__main__':
    pass
