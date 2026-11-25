"""Build full per-change review with mechanically checked line citations."""
import json
from pathlib import Path
BASE='meta/evidence/astra-1105-093817'
def rows(p):return [json.loads(l) for l in Path(p).read_text().splitlines()]
def main():
 audit=rows(BASE+'-audit.jsonl'); evidence=[]; grades=[]; keeps=[]
 def ev(p,n,meaning,excerpt=None):
  line=Path(p).read_text().splitlines()[n-1]
  evidence.append(dict(ref=f'{p}:{n}',excerpt=excerpt or line,meaning=meaning));return f'{p}:{n}'
 def keep(item,reason,decision='keep'):keeps.append(dict(item=item,reason=reason,decision=decision))
 comments={
 '24591d8':('unclear','게시45→174로 게시목표는 달성, 포럼.627→.566으로 득표개선은 미확인. 200만원 상한은 후속 사람 결정으로 이미 제거됐다. 빠진 값은 동연령 투표효과; 이번 게시표본을 기록했고 다음10개 글의1시간 중앙점수가 이전10개보다 낮으면 문구를 재검토한다.'),
 'ca5a2e1':('worked','손상행·전송중대기·자기상품 제외의 방어동작은 회귀시험을 통과했다. 게시45→174와 운영 지속은 기대 방향이나 직접 손익 인과는 아니다.'),
 'ddf6e37':('unclear','G1515 복원후79판매·숙성24건+1,558,790, 동일5단위 두창잔류0으로4유지. G6 복원후27판매·숙성11건+659,962이나 동일2단위 잔류16쌍으로 이번1로 축소한다. 빠진 값은 복원 단독효과; 이번 unit/원장 감사를 했으며 다음 두시간 품절>12분·창당판매20%감소·숙성흑자면 재확대 검토.'),
 '512fce1':('no_effect','병합 자체의 독립 경제변경은 없고 양쪽 구현은 별도 평가했다.'),
 '7bf4096':('no_effect','Astra 실행 기록이다. 경제효과는 ddf6e37에서 평가한다.'),
 'a174e3a':('no_effect','무변경 인용검증 실패 기록. 테스트의 첫 줄에 없는 문자를 인용해 수락되지 않았다.'),
 'eb15b65':('unclear','순위모집단·중간순위·구매자 가중치 수정은 유지. 현재 현금식.725와보드.706은 달라 전행재현 주장은 폐기한다. 견적확장·식품4개 효과는 혼재. 빠진 값은 설정별 독립효과; 이번 전후와 book재현을 측정, 다음 점수차>.0005면 재현실패로 표시한다.'),
 'fb6e9b7':('unclear','상한해제 전후100주문 지급56→32,80만원초과3→6, 비식품명목잉여4,210,609→4,141,226. 빠진 값은 식품실제가치 포함 숙성잉여와 주문구성 통제. 이번100주문을 측정했고 다음 두100주문 잉여/주문20%연속하락이면 정가비율축소 검토. 사람의 hard_limits 상한제거는 유지한다.'),
 'eb0e2a1':('no_effect','두 번째 무변경 인용검증 실패. 전략 점검은 별도 평가한다.'),
 '88e3fc4':('no_effect','무변경 기록으로 단기 상승은 시장·기존 운영 결과다.'),
 '87a7231':('worked','시험0→1판매, 76,183-61,963-환불0=숙성14,220. 기대18,000보다 낮지만 양수 판정선 통과. 한 표본으로 반복수익을 보장하지 않고1개만 재시험한다.'),
 '467a45d':('no_effect','시험 수락 기록이며 실제 시험 효과는87a7231에서 평가한다.'),
 '99162ae':('no_effect','주문 연결과 보증만료 대기는 적절했지만 별도 경제변경 없음.'),
 '9d860e7':('worked','수익변경이 아닌 측정. 복원코호트 숙성0→현재11건+659,962로 기다리던 데이터를 확보했다. 잔류측정은 따로 보완했다.'),
 '6bc2a06':('no_effect','측정 실행 기록이며 직접 경제변경 없음.'),
 '91e74ac':('no_effect','무변경 기록. 잔류 조회 누락은 아래 전략점검에서 평가한다.'),
 '4763ef2':('no_effect','무변경 기록. 당시 원장지연711초에서 확대 보류는 타당하다.'),
 'b0bdda5':('no_effect','현금감소와 book증가를 구분한 무변경 기록이다.'),
 '5b7ae30':('no_effect','무변경 기록. 선두 변경을 반영했으나 창당판매 조건은 계산하지 않았다.')}
 for i,x in enumerate(audit,1):
  if x['kind']!='commit':continue
  b,a=x['before'],x['after']; grade,reason=comments[x['sha']]
  ref=ev(BASE+'-audit.jsonl',i,x['sha']+' 전후30분 원장정산·원가확인 환불후기여. 시장판매량/미숙성 수를 함께 기록.',json.dumps(x,ensure_ascii=False).split('"before": ',1)[1])
  grades.append(dict(change=x['sha']+' '+x['title'].split(':',1)[0],grade=grade,reason=f"유료판매 {b['paid']}→{a['paid']}, 환불후 순기여 {b['net_after_refunds']:,}→{a['net_after_refunds']:,} ({ref}). {reason} 전후30분은 동시수정·시장변동·보증미숙성에 혼재한다."))
 for i,x in enumerate(audit,1):
  if x['kind']!='terra':continue
  b,a=x['before'],x['after'];c=x['cycle']
  ref=ev(BASE+'-audit.jsonl',i,c+' 전략검토 전후1시간',json.dumps(x,ensure_ascii=False).split('"before": ',1)[1])
  grade='unclear';reason='고가 전환 보류는 보수적이나 시간별 창당판매·동일unit 잔류를 반복 미확인으로 남겼다.'
  if c.endswith('012757'):grade='worked';reason='전략전환 대신 검증한 한단위 탐색을 수행했고 숙성14,220으로 양수 판정선을 통과했다.'
  elif c.endswith('023943'):grade='worked';reason='시험 주문을 직접 연결하고07:42 보증만료 판정선을 남겼다. 현재 숙성14,220으로 유지조건 확인.'
  elif c.endswith('215250') or c.endswith('230454'):grade='harmful';reason='전략자료는 계산했으나 인용실패로 수락되지 않았다. 같은 첫줄 인용오류가 반복됐다. 경제손실액은 미확정이다.'
  tail=''
  if grade=='unclear':tail=' 빠진 값은 당시 계산한 창당판매·잔류 판단이다. 이번37창/시간별 정산을 조회했다. 현재37.33→41.67→38.67로 연속비증가 미달; 다음 두시간 비증가와 수요·하한 통과가 함께 성립하면 watch/shift 규칙으로 판단한다.'
  grades.append(dict(change=c+' 평가·전략watch',grade=grade,reason=f"유료 {b['paid']}→{a['paid']}, 환불후기여 {b['net_after_refunds']}→{a['net_after_refunds']} ({ref}). {reason}{tail} 시장수요·이전수정·미숙성 때문에 watch의 수익효과로 해석하지 않는다."))
 for i,x in enumerate(audit,1):
  if x['kind']!='tool':continue
  name=x['name'];ev(BASE+'-audit.jsonl',i,name+' 실제 사용·실행실패')
  if name=='auto_market_lookup':keep('tools/auto_market_lookup (manifest/selftest·tool.py)',f"{x['calls']}호출·실패0이나 SKU소실·미정산혼입 재현. 원본 보존 후 두 파일 삭제, 별도 테스트파일 없음. 중복 기능은 수정 scan이 제공한다.",'drop')
  else:keep('tools/'+name,f"{x['calls']}호출·실패{x['failed']}. "+('SKU연결·정산필터를 고쳐 의미회귀시험과 sandbox 통과. 다음2주기 실제인용≥1/비정산0 기준.' if name=='auto_market_scan' else '실행회귀 없음. 무호출 도구의 이익효과는 입증하지 않으며 읽기전용 기존 기능을 유지한다.'))
 for n in range(1,8):ev(BASE+'-measurement.jsonl',n,'자동검색 전후 의미검증 / 현재 공개보드 현금식과 book식 비교')
 for n in [81,82,83,84,89,90,91,92,102,103,104,105,106,124,125,126,127]:
  ev(BASE+('-audit.jsonl' if n>=102 else '-audit-v2.jsonl'),n,'숙성 코호트·실제시험·잔류·수요·구매자 측정')
 # Original ledger receipt is a direct primary-source citation.
 ev('.market/ledger_rows.jsonl',225874,'시험 단위의 실제 지급 영수증')
 grades += [dict(change='tool_forge auto_market_lookup/auto_market_scan 생성',grade='harmful',reason='각668/30호출, 예외0이지만 fixture 상품검색0·미정산행1. 수정 scan은상품1·미정산0이다. 실행성공을 정보정확성으로 잘못 평가했다. 직접 손익 영향은 분리불가.'),dict(change='G75 목표2 및 이전 Astra 유지판정',grade='unclear',reason='직전검토56판매·숙성32건734,039에서 이번구간49판매·숙성31건557,708. 흑자이나 동일추가unit 두창잔류4쌍이라 기존철회조건을 실행해1로 축소. 독립효과는 미확인; 이번잔류측정후 다음두시간 품절>12분·창당판매20%감소·숙성흑자면 재확대 검토.')]
 keep('G6 목표2 / G75 목표2','두창 같은추가unit 잔류16쌍/4쌍. 실제 pricing.json을 각각1로 낮추고 고정설정 테스트 동반수정.','drop')
 keep('G1515 목표4·식품우선순위/수량4·현금450만/재고400만·가격/협상','37창 동일5unit잔류0, 숙성24건+1,558,790. 다른 설정별 인과효과는 미확정이나 철회조건 증거 없음.')
 keep('87a7231 next_window 및 완료이력','시험 순기여14,220 양수. 완료계획을 보존하고 같은1단위 재시험 id만 새로 부여.')
 keep('24591d8/ca5a2e1 buyer_post.py·review.py·테스트','게시45→174. 손상행·대기중글·자기상품제외 방어동작 유지, 득표효과는 동연령추적 판정.')
 keep('fb6e9b7 사람의 UNCAPPED·정가비율1.0·시장구매코드','사람의 보호한도 변경 유지. 주문예산/정가/6시간보증 준수;100주문 비교는 인과미확정.')
 keep('eb15b65 market_buyer 견적묶음·가치제한·테스트','상한 안 호가가 나올 때까지 견적묶음 진행, 보증환급 기대가치 유지. 동시변경으로 독립효과는 불명.')
 keep('eb15b65 scorecard 모집단/중간순위/가중치·meta/astra.md','요청된 계산식 유지. 현재 book보드와 차이를 새 필드로 명시; 실보드전행재현 주장은 현재 데이터로 대체.')
 keep('ddf6e37 profit_cohorts·strategy_screen·도구·검증','주문별 환불/판매시각 start와 수요 하한 유지. 기존원본로그를 재작성하지 않는다.')
 keep('meta/terra.md·strategy.md·REPORT-notes 및 감사/검증 기록','반복 측정누락·보드차이·현재사람정책을 반영해 갱신. 기존 기록커밋과 실패로그는 보존.')
 options=[dict(direction='현행 이익재고 유지·잔류축소·Ge62449c9 한단위 재시험',expected_profit='12시간 현금 +1,400만~2,200만원 시나리오. 직전12시간19분+17,178,773; 시험 추가0~18,000 기대(직전14,220).',risk='환불시 실제원가손실, 정기공급 한창 지연, G6/G75축소로 품절 가능. 현금450만/재고400만/탐색35만 유지.',test='G6/G75 각각2→1, Ge62449c9 최대1단위. unit·원장·6시간숙성으로 분리판정.'),dict(direction='고가 G00327e80 진입',expected_profit='기존흐름+0~90만원은0~3판매×가정30만원. 공개2시간31판매·11구매자·25%하한통과3건; 자체실현마진 없음.',risk='최악원가1,685,100으로 탐색35만원 초과, 환불/서지/재고잠김.',test='매입하지 않고 수정검색으로 등급별지급가·SKU연결 조회.'),dict(direction='G6/G75 추가집중',expected_profit='기존흐름+0~42만원은 각0~5추가판매×숙성건당59,997/17,991 가정.',risk='이미동일추가unit잔류16쌍/4쌍. 회전이 늘지 않으면 현금만 잠김.',test='확대하지 않고 기존철회조건에 따라1로 축소; 두시간 품절/창당판매와숙성손익 판정.')]
 strategy=dict(options=options,chosen='대안이 보수적 수익성과 한도를 함께 통과하지 못해 현행전략을 유지한다. 다만 선두격차269,561,467로1위 추격은 입증되지 않았다. 잔류 철회조건을 실행하고 양수 숙성시험만1개 재시도한다.',experiment='11/05 21:38 Ge62449c9 실제매입≤1, 같은unit두창잔류0, 판매+6시간순기여>0이면 다음1개검토; 미판매/적자면 중단. 15:38이후판매는판매+6시간판정. G6/G75는두시간연속품절>12분·창당판매20%감소·최근숙성10판매흑자면1→2검토. 작은실험은수정scan: 다음2Terra주기 실제인용≥1·비정산0·실패0.')
 out=dict(changed=True,hypothesis='추가재고 잔류조건을 실제 unit으로 실행하면 현금잠김을 줄이고, 숙성흑자가 확인된 한단위만 재시험하면 작은 위험으로 상품확장을 검증할 수 있다. 검색의 의미오류와 보드점수 차이를 드러내면 Terra의 판단근거가 개선된다.',change_summary='G6/G75 목표2→1, Ge62449c9 한단위 재시험. 잘못된 auto_market_lookup 삭제, auto_market_scan SKU·정산연결 수정과 테스트 추가. scorecard에보드차이/Players격차 추가. 전략·Terra지침·보고서·전수감사 갱신.',target_metric='현금순이익·Players, SKU별숙성순기여·두창잔류·품절분, 검색정산정확성, 보드차이',expected='12시간 현금+1,400만~2,200만원은추세시나리오. 축소두단위 약37만원 재매입억제 여지, 시험추가0~18,000 기대이나 환불시원가손실 가능. 도구fixture SKU검색0→1·비정산1→0,328테스트통과.',evidence=evidence,evidence_gap=dict(missing='축소와 재시험의 독립수익효과, 구매자 식품가치 포함 숙성잉여, 다음시험의6시간보증 결과.',measurement='이번37공급창과실제unit정산을연결해G6/G75잔류16/4쌍,시험숙성순기여14,220,상한전후100주문지급56→32를측정했다.재시험은한단위만설정했다.',judge_line=strategy['experiment']),summary='19커밋·10Terra전략점검과14도구를평가했다. 현금순이익은17,178,773증가했지만선두격차269,561,467이다. 기존잔류철회조건을실행하고숙성흑자시험을1개재시도한다.328테스트통과;커밋·push·PR없음.',strategy=strategy,grades=grades,keep_or_drop=keeps)
 for n,l in enumerate(Path(BASE+'-tests-1.txt').read_text().splitlines(),1):
  if l.startswith('Ran 328'):ev(BASE+'-tests-1.txt',n,'지정 절대경로 venv 전체시험 통과')
 Path(BASE+'-review.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
