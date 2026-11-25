"""Build the review and compact, sourced measurements; never mutate live logs."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE='meta/evidence/astra-1104-211919'
def read(p):return json.loads((ROOT/p).read_text())
def rows(p):return [json.loads(l) for l in (ROOT/p).read_text().splitlines()]
def main():
 audit=rows(BASE+'-audit-v2.jsonl'); facts=[]; evidence=[]; grades=[]
 def ev(path,n,meaning,excerpt=None):
  line=(ROOT/path).read_text().splitlines()[n-1]
  evidence.append(dict(ref=f'{path}:{n}',excerpt=excerpt or line,meaning=meaning))
 def fact(x,meaning):
  facts.append(x); n=len(facts); return f'{BASE}-metrics.jsonl:{n}'
 verdicts={
 'eba7dd6':('worked','G75는14→70누적, 확대후56판매·숙성32건+734,039로18건 목표를 넘었다. 시장 증가와 동시 변경 때문에 독립효과는 아니다. 숙성 도구의 과거 환불 오염과64MB운영기록 포함은 별도 결함으로 판정한다.'),
 '9c01990':('worked','로그 회전을 거래중단으로 오판하던 경로를 수정했다. 이후 회전에도 사이클이 기록되고 회귀테스트가 통과한다. 판매 증가는 이 수정의 직접효과가 아니다.'),
 'fff7b7f':('no_effect','첫 Astra 결과의 수동 기록으로 경제 동작 변화 없음. 손상된 이력을 숨기지 않고 보존한 기록이다.'),
 'b62d82a':('no_effect','무변경 기록이며 판매 감소를 정책의 효과로 볼 수 없다. 전략 점검은 별도 평가한다.'),
 'f0a8917':('harmful','로그·기억 제외 목적은 맞지만 stage가 이후6사이클 실패했다(logs/meta.log:85,93,100,107,114,121). d6e4441의 수정된 제외 방식을 유지한다.'),
 'e4b317e':('worked','잘못된 review JSON을 이전 점수표로 선택하던 문제를 고쳤다. 이후 팩 비교가 생기고 배열 JSON 회귀테스트도 통과한다. 단기 손익 감소는 독립효과가 아니다.'),
 'e875246':('unclear','주문별 파손 연결 수정은 재계산45환불·보드45와 일치한다. 견적 기대잉여 수식의 독립효과는 동시 상한 변경으로 분리되지 않는다. 이번100주문 지불38→50을 측정했고11/05 00:00 이후 숙성 잉여/주문 비감소로 판단한다.'),
 '8801c2b':('unclear','구매100주문 지불38→50,40만원초과0→1은 기대 방향이나 견적수정과 혼재하고88결제 모두 미숙성이다. G1515 추가분은 두창 동일5개 잔류로복원조건충족,6→4폐기. 포럼10분의 투표효과는 미확정: 게시물별1시간 관측이5개 이상 모이면 중앙점수 비감소로 판단한다. round_end는후속커밋에서이미폐기됐다.'),
 '0d2ef6e':('unclear','상한 안 호가 우선은 테스트 통과, 원가밑 마감판매는 실행시각전 제거됐다. 실제 지불38→50이 견적정렬 때문인지는 미확정. 이번100주문 측정 후11/05 00:00 숙성 잉여/주문 비감소와 지불율38%이상으로 유지 판단한다.'),
 'd6e4441':('worked','stage실패6회 이후18:19·19:30·20:42기록이 정상 복구됐다. 함께 커밋된 G6축소는별도평가하고이번에폐기한다.'),
 'f609ab0':('no_effect','무변경 답변의 테스트행 인용이 실패해 no_change_unsupported였다. 실제 경제변경없음. 테스트통과와인용검증을구별해야한다.'),
 'b435c06':('worked','측정 전0에서 주문503·재고466·공급창11·숙성판매1299·포럼추적20행이 생겼다. 원장결제시각과교차해두창잔류를판정했다. 기록기능성공이며직접수익효과는아니다.'),
 'dd0018e':('no_effect','무변경 기록이다. 이후판매증가/기여감소는시장·환불·재고에의한관측이며기록의효과가아니다.'),
 '6fac5ef':('no_effect','무변경 기록이다. 반복watch의측정누락은별도평가한다.'),
 '44cb4a6':('no_effect','무변경 기록이다. 관측종료까지짧고미숙성이므로기여증가를watch의효과로보지않는다.')}
 for x in audit:
  if x['kind']!='commit':continue
  b,a=x['before'],x['after'];sha=x['commit']
  ref=fact(dict(change=sha,paid_before=b['paid'],paid_after=a['paid'],known_net_before=b['known_net'],known_net_after=a['known_net'],market_paid_before=b['market_paid'],market_paid_after=a['market_paid'],missing_cost=[b['missing_cost'],a['missing_cost']],source=f'{BASE}-audit-v2.jsonl:{audit.index(x)+1}'),'')
  grade,reason=verdicts[sha]
  grades.append(dict(change=sha,grade=grade,reason=f"유료{b['paid']}→{a['paid']}, 환불후 원가확인 기여{b['known_net']:,}→{a['known_net']:,}({ref}). "+reason))
 # Recover the six unrecorded Terra answers from the actual Codex output.
 text=(ROOT/'logs/meta-codex.log').read_text();dec=json.JSONDecoder();pos=0; recovered={}
 while pos<len(text):
  pos=text.find('{',pos)
  if pos<0:break
  try:x,n=dec.raw_decode(text[pos:]);pos+=n
  except ValueError:pos+=1;continue
  if isinstance(x,dict) and 'strategy_check' in x:
   for c in ['110508','121030','131030','141031','151031','161033']:
    if c in str(x.get('evidence')):recovered[c]=x
 (ROOT/(BASE+'-recovered-terra.json')).write_text(json.dumps(recovered,ensure_ascii=False,indent=2)+'\n')
 checks={
 '095319':('worked','G75숙성 대기와현재전략유지는타당했다. 다만pack비교누락을손익효과로쓰지않은점만평가하며현금증가를무변경성과로보지않는다.'),
 '110508':('worked','G1515숙성표본을15→22건으로확인한측정은진전이다. stage실패로공식grade가누락됐다. 과거환불수치는이번재계산으로대체한다.'),
 '121030':('unclear','G75누적26→31·관측기여620,221→785,256이나26건미숙성이었다. 이번확대후숙성32건+734,039를측정했다.11/05 03:20까지기존시험잔여보증이끝난후총순기여양수면유지한다.'),
 '131030':('unclear','G75누적31→37·숙성기여119,996→190,335는방향일치하나확대전후가혼재했다. 이번start코호트32건+734,039측정.11/05 03:20잔여숙성후순기여양수면유지한다.'),
 '141031':('unclear','G75숙성11건+271,121·G6숙성27건+2,187,308만으로추가재고효과는분리못했다. 이번unit잔류조회에서G75두창잔류0을확인;다음6창같은추가단위잔류0/숙성기여양수면유지한다.'),
 '151031':('harmful','G6목표2→1의근거인최근10건-296,948이동일시각주문재연결시+730,260·환불0으로바뀐다. 이잘못된축소를이번에1→2로복원한다. 경제적기회손실액은통제군없어미확정이다.'),
 '161033':('unclear','최근10건추정-296,948→-626,071을축소후성과로단정하지않은것은옳다. 그러나그환불연결자체가잘못됐다. 이번원장재계산과start분리를완료;복원후숙성10건순기여양수·두창잔류0이면2유지한다.'),
 '170508':('harmful','경쟁고가상품watch는합리적이나확인가능한우리하한을계산하지않았고테스트행인용실패로점검이수락되지않았다. 이watch를shift선행조건으로세면안된다.'),
 '181700':('unclear','G1515환불15/99로축소경고했지만원장재연결의소급적용이없었다. 이번전체숙성132건환불12건으로재계산했고unit두창잔류로6→4판정했다. 다음6창목표초과신규구매0으로수정판단을검증한다.'),
 '192904':('harmful','고가G03 13건·8명watch이후에도우리하한을미확인으로남겼다. 이번공개카탈로그계산으로하한1,481,884>최근최고1,408,014가확인되어값이없었던것이아니라계산누락이었다. 매입보류자체는옳다.'),
 '204039':('harmful','G03 13→12건·8→5명만반복하고하한·창당판매를재차누락했다. 창당37.67→39.67→35.67은연속감소도아니다.20:20/20:40동일5단위잔류를이번에확인해G1515를복원했다. 실제shift는없었다.')}
 prev=read('meta/evidence/astra-1104-090509.json')
 for p in sorted((ROOT/'meta/evidence').glob('terra-1104-*.json')):
  c=p.stem.split('-')[-1]
  if c not in checks:continue
  x=json.loads(p.read_text());b=prev['board']['shops']['mine'];a=x['board']['shops']['mine']
  ref=fact(dict(change=p.stem,paid_before=b['deals'],paid_after=a['deals'],profit_before=b['profit'],profit_after=a['profit'],source=str(p.relative_to(ROOT))), '')
  g,r=checks[c];grades.append(dict(change=p.stem+' 전략점검/변경',grade=g,reason=f"누적유료{b['deals']}→{a['deals']}, 보드이익{b['profit']:,}→{a['profit']:,}({ref}). "+r));prev=x
 # Explicit sub-change grades for mixed commits.
 grades += [dict(change='eba7dd6 숙성도구/점수표',grade='harmful',reason='과거환불60건12,629,649를그대로읽어현재원장45건8,467,071과불일치했다. 최초기능은작동했으나판정오염을막지못했다. 이번주문연결재계산과회귀테스트로교체했다.'),dict(change='8801c2b G1515 4→6',grade='harmful',reason='확대근거인6시간45%품절은관측후4.21/228.32분으로줄었지만20:20/20:40동일5단위잔류가복원조건을충족했다. 판매증가효과와별개로추가재고유지실패로판정하고6→4복원했다.')]
 (ROOT/(BASE+'-metrics.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in facts))
 for i,x in enumerate(facts,1):ev(BASE+'-metrics.jsonl',i,'변경별 전후 측정. 시장변동·동시수정·미숙성의 영향이 있어 인과효과로 단정하지 않는다.')
 for n in [66,67,70,72,74,81,82,101,102,103,104,105,106]:ev(BASE+'-audit-v2.jsonl',n,'원장·기억·공급창에서 재현한 환불, 숙성, 수요 또는 품절 측정.')
 ev(BASE+'-retention-v2.jsonl',2,'사람이 정한 G1515 복원 조건이 동일 unit으로 충족됐다.')
 ev(BASE+'-retention-v2.jsonl',1,'G75의 관측 가능한 11창에서 동일 추가재고 잔류는 없다.')
 ev(BASE+'-g6-recheck.jsonl',1,'Terra의 G6 축소 근거가 잘못된 환불 연결에서 나왔음을 동일 시각에 재현했다.')
 for n in [1,2]:ev(BASE+'-buyer-sample.jsonl',n,'상한·견적 변경 전후100주문의 실제 결제를 조회했다. 보증 미숙성이다.')
 for n in [85,93,100,107,114,121]:ev('logs/meta.log',n,'stage 문제로 누락된 Terra 사이클의 실제 실패 기록.')
 keeps=[]
 def keep(item,reason,decision='keep'):keeps.append(dict(item=item,reason=reason,decision=decision))
 for x in audit:
  if x['kind']=='tool':keep('tools/'+x['name'],f"이번구간{x['calls']}호출·실패{x['failed']}건. "+('환불계산과start필터를이번에수정해유지한다.' if x['name']=='cohort_profit' else '무호출은무용성의증거로보지않고읽기전용능력을유지한다.' if not x['calls'] else '실행오류는없으며도구의독립이익효과는미확정이다.'))
 keep('tools/auto_* 제거 상태','auto_market_lookup·auto_catalog_ledger_search는현재디렉터리에없다. 과거호출누계는재설치근거가아니므로제거상태를유지한다.')
 keep('G75 목표2 / eba7dd6','확대후56유료·숙성순기여734,039,관측11창추가단위잔류0. 목표2유지.')
 keep('G6 목표1 / Terra15:11·d6e4441','동일시각최근10건순기여-296,948가+730,260으로수정되어축소근거가무효. 이번목표2와테스트로복원했다.','drop')
 keep('G1515 목표6 / 8801c2b','사람의두창연속추가분잔류조건충족. 이번목표4와테스트로복원했다.','drop')
 keep('max_units_per_sku=6 / high_price_proven_max_units=6','전역천장은유지하며G1515합계목표4가먼저제한한다. 다른SKU천장의실패근거없음.')
 keep('buyer.max_spend_per_deal=800000','100주문유료38→50·40만원초과0→1. 동시견적변경과미숙성으로최종잉여판정은11/05 00:00이후.')
 keep('forum.every_minutes=10','포럼전송거절없음. 투표기여는별도측정하며빈도가투표증가라고주장하지않는다.')
 keep('round_end 제외 / 0d2ef6e','후속수정이이미제거했다. 마감정리설계가없는상태에서재활성화하지않는다.')
 keep('e875246 주문별파손연결·보증기대잉여 / 0d2ef6e 상한내호가우선','정확한주문연결과계약상보증환급수식은유지한다. 과거손실누계오염은이번점수표에서고친다.')
 keep('9c01990 로그회전·복원 / e4b317e 이전팩선별 / d6e4441 stage','회귀테스트통과와후속기록복구. f0a8917의실패한stage호출은이미대체되어현재수정본유지.')
 keep('f0a8917 로그·기억제외/.gitignore·프롬프트','운영기록64MB재포함방지목적유지. 키트로그/기억의원본은수정하지않는다.')
 keep('b435c06 measure.py 및 harness/restock/buyer/review 계측','주문503·재고466·창11·숙성1299행. 잔류조건판정에실제사용했고기록예외가거래를멈추지않는테스트유지.')
 keep('scorecard/profit_cohorts·cohort_profit 수정','현재원장환불재계산,과거추정차이와원가/보증결측을노출. 원본손실로그를편집하지않는다.')
 keep('tools/market_opportunity 및 scorecard 데이터','실제수요와우리서지·보증하한을동시에제공하는작은읽기전용실험. 다음두Terra주기인용·오류0으로판정.')
 keep('meta/strategy.md / meta/terra.md / REPORT-notes','현재운영수치·복원조건·잘못된환불누계금지·누락값당일계산을반영해유지한다.')
 strategy=dict(options=[dict(direction='폭넓은 이익재고 + 검증매트리스/침구, 잘못된 축소와 잔류분 복원',expected_profit='다음12시간 현금증가1,800만~2,600만 시나리오. 직전약12시간+1,972만과최근1.83시간+298만에근거. 두목표복원독립효과는미확정.',risk='G1515 축소가품절을늘리고G6복원은약25만원자금을묶을수있다. 보증6시간·현금450만·재고400만상한유지.',test='G1515 목표6→4와G6 1→2를각SKU코호트·동일unit두창잔류로분리판정.'),dict(direction='고가매트리스 G03d0199f 진입',expected_profit='기존흐름+0~60만원은가정10만원×0~6판매의낙관시나리오. 최근2시간114거래·26구매자이나최고지급가1,408,014<무서지하한1,481,884.',risk='한단위최악121만~181만원잠김. 경쟁원가미공개이며짧은보증모방불가.',test='읽기전용screen으로두시간연속10판매·3구매자·25%하한이상3결제를확인하기전매입보류.'),dict(direction='중고가침구 G05277ffe 한단위',expected_profit='기존흐름+0~18만원은가정3만원×0~6판매. 최근2시간50판매·23구매자이나15%하한이상1건,25%하한이상0건.',risk='최악45만~67.5만원재고와환불. 기존G75보다유리한자체손익없음.',test='새도구로서지포함하한과지급가를먼저비교;현재매입하지않음.')],chosen='다른방향이현재전략을보수적보증포함수익성에서앞선다는자료가없어현행구성을유지한다. G1515는실제잔류조건에따라4로, G6는오류에의한축소를취소해2로복원한다. 현행만으로선두추격이보장되지는않는다.',experiment='11/05 09:19 판정: G1515 최소6창에서목표초과신규구매0·동일추가단위두창잔류0·첫6시간판매숙성기여≥0이면4유지. 두시간연속품절>20%·창당판매20%감소·최근숙성10건흑자면4→5. G6복원후숙성10건적자또는두창추가단위잔류면2→1. 작은실험은market_opportunity: 다음두Terra주기에수요/하한/서지인용과도구오류0, 직접추가매입0.')
 out=dict(changed=True,hypothesis='잘못된환불연결을고치면수익성있는G6의잘못된축소를되돌릴수있다. 실제두창잔류가확인된G1515만줄이면재고자금을줄이면서검증된판매를이어갈수있다. 경쟁수요에보증·서지하한을붙이면막연한고가진입watch를측정가능한판단으로바꾼다.',change_summary='G1515 목표6→4, G6 1→2복원. 숙성손익을현재원장주문별로재계산하고cohort_profit에start필터추가. 읽기전용market_opportunity와점수표연결·테스트추가. 전략·Terra지침·보고서재료·전수평가갱신. 커밋/push/PR없음.',target_metric='현금순이익·Players, SKU별6시간숙성순기여·동일unit두창잔류·품절분, 환불연결일치, 도구실패',expected='G1515 두단위축소로약49만원재고사용감소여지, G6복원한단위는약25만원추가자금. 직접현금이익증가로단정하지않는다. 점수표환불은동일마감45건8,467,071로일치, 다음두Terra주기도구오류0과수요/하한인용. 12시간현금증가1,800만~2,600만은시장변동을포함한시나리오다.',evidence=evidence,evidence_gap=dict(missing='두목표복원후추가판매·실현현금효과와6시간보증숙성, 구매상한/견적변경의독립잉여효과.',measurement='이번사이클원장주문연결로45환불재계산, G6당시10판매+730,260, G1515동일5unit두창잔류확인. 전후100구매주문지불38→50측정. start필터와서지/보증하한도구를추가했다.',judge_line='11/05 09:19 G1515 최소6창초과구매0·추가단위두창잔류0·숙성순기여≥0이면4유지. G6숙성≥10건순기여<0또는두창잔류면2→1. 구매자는11/05 00:00이후숙성잉여/주문비감소와지불율≥38%로판정.'),summary='15커밋과누락된6회를포함한11Terra주기를평가했다. 과거환불오염으로잘못축소한G6를복원하고, 실제잔류조건이충족된G1515는축소했다. 고가진입은보증포함하한미달로보류했다.',strategy=strategy,grades=grades,keep_or_drop=keeps)
 (ROOT/(BASE+'-review.json')).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
