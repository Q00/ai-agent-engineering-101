import json,pathlib,collections
P='meta/evidence/astra-1110-021612'
def rows(s):return [json.loads(x) for x in open(P+s) if x.strip()]
cs=rows('-commits.jsonl');ms=rows('-commit-metrics.jsonl');cycles=rows('-cycles.jsonl');tools=rows('-tools.jsonl')
grades=[];ev=[];compact=[]
for i,(c,m) in enumerate(zip(cs,ms),1):
 h=c['commit'][:8];b=m['before'];a=m['after'];title=c['body'].splitlines()[0]
 metric=f"상점결제 {b['shop_paid']}→{a['shop_paid']}, 구매결제 {b['buyer_paid']}→{a['buyer_paid']}, 숙성순기여 {b['mature_net']}→{a['mature_net']}원 (숙성 {b['mature_paid']}→{a['mature_paid']}건)"
 compact.append(dict(commit=h,comparison=metric,source=f'{P}-commit-metrics.jsonl:{i}',before_hours=b['observed_hours'],after_hours=a['observed_hours']))
 evidence_ref=f'{P}-comparisons.jsonl:{i}'
 ev.append(dict(ref=evidence_ref,excerpt=metric,meaning='해당 커밋 전후30분 원장·판매원가·주문연결환불 측정. 미숙성 None은0원이 아니며 동시변경의 인과효과는 미분리.'))
 grade='unclear';detail='결측: 이 변경만의 대조군과 버전별 숙성효과. 측정: 위 전후30분 결제/순익. 판정:11/10 14:16까지 군별 숙성20결제에서 추가 Players>0 및 원래 undo 미충족; 부족하면 미확정, 확대 금지.'
 if any(title.startswith(x) for x in ['chore','docs','test(']) or h in ['742a7885']:
  grade='no_effect';detail='기록·문서·테스트 변경으로 직접 운영 지표 효과를 기대하지 않는다. 원장 변화는 같은 시간대 운영 변화다.'
 elif h in ['60012afe','47e976e4','d5b60bec','e412db0e','d0916abb','c0da41b4']:
  grade='no_effect';detail='시간초과/테스트 실패로 되돌려져 운영 지속효과 없음. 원본 시도와 실패는 보존한다. G2 목표는3이다.'
 elif h=='6e3fc0c7':
  grade='worked';detail='Terra 기록의 최초3판단에서 전체 재고만으로 축소0건으로 좁은 성공선을 통과했다. 이후24판단에서 회피 문구가 반복되어 영구개선/Players 효과는 미확정; 다음3건 제외SKU 근거와 최종 공급을 대조한다.'
 elif h=='11422a7a':
  grade='worked';detail='중단조건 실행: C 무보증 처리군7/18판매·4.99M 대 대조14/16·6.74M(p=.005).72h 복원은 유지. 복원 자체의 순위효과는 동시변경과 미분리.'
 elif h=='20b32e6a':
  grade='worked';detail='forum.json 부재 seed 실패1건→0건, 기록상 전체1576테스트 통과. 다만 의미검증4건은 이번에 모두 실패해 SKU/결제/재고를 추가수리했다. 실행성공과 내용정확성을 분리한다.'
 elif any(x in title for x in ['goods-grade','goods grade','goods 등급']):
  detail='등급 A/B 주문379/374, 지급101/98, 숙성65/67 및 정규화잉여합17.288434/19.638007. 결측: 사다리·코드버전 교란을 분리한 Saved분모 효과.11/10 14:16까지 교차군별 숙성20건에서 추가 Players>0와 원래 중단조건을 판단; 부족하면 미확정.'
 elif any(x in title for x in ['goods ladder','goods-ladder','goods opening ladder']):
  detail='상품 사다리 A/B 주문367/386, 지급107/131, 숙성66/74 및 정규화잉여합18.895469/22.401718. 방향은 유망하나 등급·버전 교란과 Saved분모 결측.11/10 14:16 교차군별숙성20건·추가Players>0가 판정선; 미달이면 미확정.'
 elif any(x in title for x in ['food ladder','food-ladder']):
  detail='식품 사다리 A/B 주문160/154, 지급147/138. 숙성97/87건의 사용기한 실제가치가 결측이다. 측정:주문→정산을 연결.11/10 14:16 군별20건의 use_within/기한을 확인해 추가Players>0 판정; 미확정이면 확대 금지.'
 elif 'saved_floor' in title:
  detail='이번 주문 감사의 saved_floor 표본0. 기본 OFF 코드의 도입을 Saved 개선으로 세지 않는다. 결측:켜진 군별 지급/실제가치.11/10 14:16 군별숙성20건 추가Players>0와 원래 undo를 판단하되 다른 연구 중에는 켜지 않는다.'
 elif any(x in title for x in ['warranty_said','warranty-said','bw1-']):
  detail='팩 시각 전 BW1 주문표본0(운영 시작은 팩 뒤). 결측:군별 지급/숙성가치. 이번은 전후 결제만 측정했다.11/10 14:16 군별숙성20건 추가Players>0/영수증 검증위반0을 판단하고 원래 undo 우선.'
 elif 'buyer_our_ask' in title:
  detail='해당 연구 주문표본0·기본 OFF. 결측:텍스트 순위의 추가지급과 Saved. 전후결제 및 전체 회귀를 측정했다.11/10 14:16 군별숙성20건 추가Players>0/선택뒤늦은견적사용0 판정, 표본미달이면 미확정 유지.'
 elif 'seller_food_margin' in title or 'seller_floor_margin' in title or 'seller_a_open' in title:
  detail='결측:처리단위별 숙성매출·환불·재고노출과 군간간섭. 측정:전후30분 정산/순익.11/10 14:16 군별숙성20건에서 순기여/재고시간 B−A>0 및 상점 전체 무해 조건, 원래 연구 undo 우선; 표본미달 미확정.'
 elif 'buyer_post_market' in title:
  detail='결측:게시처리군별 유효 stance와 Saved/평점 귀속. 이번 전후지급과 포럼 .895를 관측했다.11/10 14:16 군별게시20건·서로다른 유효투표자3명에서 추가forum rating>0 및 허위주장0 판정; 미달이면 미확정.'
 grades.append(dict(change=h+' '+title[:130],grade=grade,reason=metric+'; '+detail+' 근거 '+evidence_ref))
with open(P+'-comparisons.jsonl','w') as f:
 for r in compact:f.write(json.dumps(r,ensure_ascii=False)+'\n')
for i,r in enumerate(cycles,1):
 if not r.get('cycle','').startswith('terra'):continue
 a=r.get('answer') or {};st=a.get('strategy_check') or {};status=r.get('status');g='unclear'
 reason='규칙 산식 일치와 무호출상한을 유지했고 전체 재고축소/근거없는 총상한 확대는 없었다. '+('전략유지의 우월한 대안은 미식별. ' if st.get('keep_or_shift')=='keep' else '')
 if r['cycle']=='terra-1110-000257':g='harmful';reason+='알고 있던 seed 실패를 수리하지 않고 같은 설정 변경을 재시도해 두 번째 반려.운영목표3→3, 수정효과0. 반복수리 지연을 감점한다.'
 elif r['cycle']=='terra-1110-011541':g='worked';reason+='seed 실패1→0/1576테스트 통과.수리는 성공했으나 도구 의미검사는 놓쳤다.'
 elif r['cycle']=='terra-1109-224449':g='no_effect';reason+='숙성55건 건당−12571.24원에 따른 SKU축소 방향은 규칙에 맞지만 반려돼목표3→3.'
 elif r['cycle']=='terra-1109-164637':g='worked';reason+='최초3판단/총재고만축소0으로 지침 검사를 수행했다. 이후24판단까지 성공을 일반화하지 않는다.'
 elif r['cycle']=='terra-1109-202132':reason+='외부 실험스위치 변경으로 unsupported; Terra가 규칙을 위반했다고 단정하지 않는다. '
 if g=='unclear':reason+='결측은 실제 SKU목표 구속과 변경별 추가Players. 이번 두창 전체상한절단0/0,SKU절단23/21 및 단위ID를 측정했다.11/10 14:16 두창구속+과반판매+숙성순익>0/book비하락을 모두 확인하면 확대, 아니면 유지.'
 grades.append(dict(change=r['cycle']+' 전략·규칙 점검',grade=g,reason=reason+f' 근거 {P}-cycles.jsonl:{i}'))
 ev.append(dict(ref=f'{P}-cycles.jsonl:{i}',excerpt=r['cycle'],meaning='해당 Terra의 원문 변경·전략·실험·반려 상태를 보존한 기록.'))
keep=[]
for i,t in enumerate(tools,1):
 reason=f"이번 {t['calls']}호출/{t['failures']}실패, 누적 {t['lifetime'].get('calls',0)}호출."
 if t['tool']=='auto_market_research_pack':reason+='의미회귀4실패→0으로 수리 유지; 집중도는 제한된 표본임을 명시.'
 elif t['calls']==0:reason+='이번 운영효과는 미확인이나 기존 의미회귀/읽기전용 역할을 유지.무사용을 수익으로 계산하지 않음.'
 else:reason+='실행성은 확인.개별 Players효과는 미확정이며 읽기전용 기능 유지.'
 keep.append(dict(item='tools/'+t['tool'],decision='keep',reason=reason+f' {P}-tools.jsonl:{i}'))
 ev.append(dict(ref=f'{P}-tools.jsonl:{i}',excerpt=f'"calls": {t["calls"]}, "failures": {t["failures"]}',meaning=t['tool']+'의 검토 구간 사용·실패 수.'))
# Every changed path is explicitly covered, including implementation tests and live study switches in commits.
paths=sorted({p.removeprefix('submissions/26622007/week-08/') for c in cs for p in c['files']})
for path in paths:
 if path.startswith(('meta/evidence/','logs/','memory/')):continue
 if path.startswith('tools/') and any(path.startswith(k['item']+'/') for k in keep):continue
 keep.append(dict(item=path,decision='keep',reason='해당 경로 변경 커밋의 개별 판정은 grades에 기록.검증된 수정/원래 실험조건과 실패이력을 보존한다.사람의 중단조건 충족이 확인되지 않은 활성정책을 임의 복원하지 않는다.반려된 설정은 이미 복원된 현재값 유지.'))
for id in ['carpet-G1465c678-one-unit','g6-a-ask-cap-343k','supply-audit-context-113730','forum-demand-join-113730']:
 keep.append(dict(item='meta/experiments.json:'+id,decision='drop',reason='팩에서 이미 done/drop인 항목을 활성 큐에서 이번에 제거했다.이전큐 스냅샷과 experiments_log 종료기록은 보존.재시작하지 않는다.'))
# Stable, concise findings and raw test evidence.
for suffix,lineno in [('-findings.jsonl',1),('-findings.jsonl',6),('-guards.jsonl',1),('-guards.jsonl',2),('-period.jsonl',1),('-study-arms.jsonl',3),('-study-arms.jsonl',4),('-study-arms.jsonl',5),('-study-arms.jsonl',6)]:
 line=open(P+suffix).read().splitlines()[lineno-1];ev.append(dict(ref=P+suffix+':'+str(lineno),excerpt=line,meaning='이번 주기에 원장·팩·공급창으로 측정한 수치.'))
for suffix,needle,meaning in [('-pack-before.txt','FAILED (failures=4)','수리 전 의미회귀4건 모두실패'),('-pack-after.txt','Ran 4 tests','수리 후 같은4건 통과'),('-tests.txt','Ran 1595 tests','집중도 추가검사를 포함한 지정환경 전체1595테스트 통과')]:
 for i,line in enumerate(open(P+suffix),1):
  if needle in line:ev.append(dict(ref=P+suffix+':'+str(i),excerpt=line.strip(),meaning=meaning));break
out=dict(cycle='astra-1110-021612',cutoff='2026-11-10T02:16:13+0900',previous_review='astra-1109-133041 (rejected; review starts at its request time 13:30)',grades=grades,keep_or_drop=keep,evidence=ev)
open(P+'-review.json','w').write(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(dict(grades=len(grades),keep_or_drop=len(keep),evidence=len(ev),changed_paths=len(paths)))
