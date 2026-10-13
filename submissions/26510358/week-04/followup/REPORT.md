# Week 04 보강: 발화 판독, 종료, 제안 상태, 반복 순서
[과제 명세](../../../../weeks/week-04/README.md)의 핵심 질문은 같은 buyer·seller 협상에서 **메시지 형식과 판독 방식이 무엇을 바꾸는가**임
기존 [본 실험 보고서](../REPORT.md)와 [36개 결과](../results.csv)를 유지하고, [사전 계획](PLAN.md) 커밋 뒤 추가 모델 실험을 수집함
추가 CSV와 원본 콘솔 로그는 연구별로 분리함
각 연구 안에서 역할 프롬프트·모델·온도·턴 상한은 같고 조건별 차이는 형식 문단과 reader로 한정함
`correct`: 거래 가능 시 양쪽 한도 내 합의, 거래 불가능 시 명시적 `no_deal`
`open`은 실패로 셈
`reader_calls`는 협상 agent 호출 외에 발생한 판독 모델 호출 수임
추가 72개 에피소드에 실행 중단은 없었음
## 1. reader가 뜻을 읽었는지 검사
기존 `free`·`tagged`의 **175개 발화 전부**를 원문 기준으로 주석화함([추출본](audit_input.csv), [주석](audit_labels.csv), [감사 코드](audit.py))
거절 뒤 새 가격을 제시한 문장은 `propose`, 둘 이상의 해석이 가능한 문장은 `ambiguous`로 판정함
Codex 단일 주석자의 규칙 기반 판정이며 독립된 사람의 정답 표본은 없음
따라서 아래 수치는 객관적 정확도 추정이 아닌 재검토 가능한 불일치 기록임
| 조건 | 원문 발화 | 명확한 판정 | 모호 | 행위 불일치 | 비교 가능한 제안 가격 중 불일치 |
|---|---:|---:|---:|---:|---:|
| `free` | 90 | 88 | 2 | 1 | 0/64 |
| `tagged` | 85 | 85 | 0 | 0 | 0/48 |
`free`의 행위 불일치 1건은 [free-01의 textbook 6턴](../logs/free-01.txt#L51-L54)임
seller는 35를 거절하며 **50을 새로 제안**했지만 reader는 `reject-proposal`로 기록함
[주석을 대입해 24개 에피소드를 재생](audit.py)한 결과 최종 결과 변화는 **0건**임
따라서 이번 오독이 성과를 낮췄다고 판단할 근거는 없음
기존 `format_errors=0`은 문법상 파싱 성공을 뜻하며 의미 일치까지 뜻하지 않음
`structured`의 기존 94개 메시지는 자연어 의도 대신 스키마만 검사했고 무효·파서 불일치는 0건이었음
스키마 유효성 역시 올바른 결정을 보장하지 않음
## 2. 같은 과제의 반복을 늘리고 조건 순서를 섞음
[추가 실행기](run_followup.py)는 원본과 같은 시나리오 4개, `gpt-5.6-luna`, temperature `0.7`, `reasoning_effort=none`, `max_completion_tokens=512`, 최대 8턴을 사용함
`replication`은 역할·형식·reader 문구를 유지하고 조건별 3회씩 추가함
세 블록의 조건 순서는 `structured → tagged → free`, `tagged → free → structured`, `free → structured → tagged`로 고정함
[추가 36개 CSV](replication.csv)와 [9개 원본 로그](../logs/replication-10.txt)는 별도로 보존함
원본 36개는 `free`·`tagged`·`structured`별 3회씩 묶어서 실행했으므로 합친 72개 전체가 완전한 무작위·균형 순서는 아님
| 연구·조건 | correct | deal / no_deal / open | violation | 평균 turns | format errors | reader calls |
|---|---:|---:|---:|---:|---:|---:|
| 원본 `free` | 2/12 | 2 / 0 / 10 | 0 | 7.50 | 0 | 90 |
| 원본 `tagged` | 4/12 | 4 / 0 / 8 | 0 | 7.08 | 0 | 48 |
| 원본 `structured` | 2/12 | 2 / 0 / 10 | 0 | 7.83 | 0 | 0 |
| 추가 `free` | 5/12 | 5 / 0 / 7 | 0 | 7.50 | 0 | 90 |
| 추가 `tagged` | 6/12 | 6 / 0 / 6 | 0 | 6.83 | 0 | 44 |
| 추가 `structured` | 2/12 | 2 / 0 / 10 | 0 | 7.67 | 0 | 0 |
| 합계 `free` | 7/24 | 7 / 0 / 17 | 0 | 7.50 | 0 | 180 |
| 합계 `tagged` | 10/24 | 10 / 0 / 14 | 0 | 6.96 | 0 | 92 |
| 합계 `structured` | 4/24 | 4 / 0 / 20 | 0 | 7.75 | 0 | 0 |
같은 프롬프트를 더 실행해도 거래 불가능한 `keyboard`와 `laptop`은 세 형식 모두 각각 **0/6 correct**, 전부 `open`이었음
거래 가능한 `bike`/`textbook`의 correct는 `free` 5/6·2/6, `tagged` 5/6·5/6, `structured` 2/6·2/6임
태그가 reader 호출을 줄인 것은 관찰됐지만 이 작은 고정 시나리오로 일반적인 성공률 순위를 확정할 수 없음
## 3. 종료 행위는 공통 정책으로 별도 검사
원본 실험에서는 거래 불가능한 경우에도 `refuse`가 없어 `no_deal=0`이었음
이를 형식 문제와 구분하려고 [사전 계획의 동일한 deadline 지침](PLAN.md)을 buyer·seller **모두**의 공통 프롬프트에 추가함
수락 가능한 상대 제안은 받아들이고 마지막 자기 차례에 수락할 제안이 없으면 `refuse`하도록 요청함
원본 비교 조건과 CSV는 유지하고 별도 [36개 결과](termination.csv)와 [9개 로그](../logs/termination-19.txt)를 수집함
조건 순서는 블록별로 `tagged → structured → free`, `free → tagged → structured`, `structured → free → tagged`로 고정함
| 조건 | correct | deal / no_deal / open | violation | 평균 turns | format errors | reader calls |
|---|---:|---:|---:|---:|---:|---:|
| `free` | 12/12 | 6 / 6 / 0 | 0 | 5.42 | 0 | 65 |
| `tagged` | 10/12 | 5 / 7 / 0 | 1 | 5.50 | 0 | 32 |
| `structured` | 10/12 | 6 / 6 / 0 | 1 | 5.83 | 1 | 0 |
| 시나리오 (거래 가능 여부) | `free` correct | `tagged` correct | `structured` correct |
|---|---:|---:|---:|
| bike (가능) | 3/3 | 3/3 | 3/3 |
| textbook (가능) | 3/3 | 1/3 | 2/3 |
| keyboard (불가능) | 3/3 | 3/3 | 2/3 |
| laptop (불가능) | 3/3 | 3/3 | 3/3 |
세 조건 모두 `open=0`으로, 불가능한 거래에서도 명시적으로 떠난 사례가 생김
그러나 가능한 textbook 거래를 `no_deal`로 끝낸 사례가 `tagged`와 `structured`에서 각 1건 나옴
한도 위반 2건도 원본 그대로 보존함
[tagged run 23](../logs/termination-23.txt#L30-L38)은 buyer 예산 45인데 seller의 50 제안을 buyer가 수락한 사례임
[structured run 20](../logs/termination-20.txt#L56-L61)은 buyer 예산 70인데 seller의 90 제안을 JSON `accept-proposal`로 수락한 사례임
둘 다 파싱은 성공했으므로 명확한 형식이 비공개 한도 준수까지 보장하지는 않음
[structured run 25](../logs/termination-25.txt#L69-L76)의 `content:null`은 필수 `content.price`가 없어 파싱 실패한 1건이며, 다음 `refuse`로 해당 에피소드는 `no_deal` 종료됨
이 연구는 공통 행동 지침을 추가해 종료 양상을 살핀 **탐색적 후속 실험**임
원본과 생성 시점·프롬프트가 달라 변화량을 정책이나 형식의 인과 효과로 단정할 수 없음
턴 제한 도달도 `open`으로 기록하는 기존 점수 규칙을 유지함
## 4. `accept-proposal`의 대상 제안을 명확히 함
원본 harness는 상대가 마지막으로 제시한 가격을 기억함
[별도 재생기](protocol_audit.py)는 새 제안이 이전 제안을 대체하고 거절이 제안을 닫는 더 엄격한 **대안 규칙**을 시험함
닫힌 제안의 뒤늦은 수락은 거래로 세지 않음
이는 과제의 “상대의 마지막 가격” 규칙이 틀렸거나 FIPA가 특정 상태기를 요구한다는 주장이 아님
[합성 사례 테스트](test_protocol_audit.py)는 과거 가격 수락이 두 규칙에서 다르게 판정될 수 있음을 보여 줌
원본·반복·종료 연구의 **108개 에피소드**를 두 규칙으로 재생함
최종 결과·가격 차이는 **0건**, 엄격한 규칙에서 유효하지 않은 수락도 **0건**이었음
제안 대체나 거절로 만료된 제안은 연구별 158·150·76개였으나 정상적인 역제안도 포함하므로 오류 수로 해석하면 안 됨
현재 결과를 수정할 사유보다 프로토콜 의미를 구분해야 할 경계 사례를 확인한 셈임
## 검증·재현
[분석기](analyze.py)는 원본·반복·종료 결과의 조건별·시나리오별 표를 CSV에서 생성함
[검증기](verify_followup.py)는 추가 CSV 72행과 원본 로그 18개를 대조하고 실행 순서·시나리오/지침 해시·파서/reader 기록·한도 위반을 검사함
아래 명령은 API 호출 없이 저장된 데이터를 확인함(저장소 루트 기준)
```bash
python3 submissions/26510358/week-04/followup/audit.py
python3 submissions/26510358/week-04/followup/protocol_audit.py --study original
python3 submissions/26510358/week-04/followup/protocol_audit.py --study replication
python3 submissions/26510358/week-04/followup/protocol_audit.py --study termination
python3 submissions/26510358/week-04/followup/verify_followup.py replication
python3 submissions/26510358/week-04/followup/verify_followup.py termination
python3 submissions/26510358/week-04/followup/analyze.py
python3 -m unittest discover -s submissions/26510358/week-04/followup -p 'test_*.py'
python3 scripts/check_week04.py submissions/26510358/week-04
```
새 API 실험은 `requirements.txt` 설치와 비공개 환경변수 설정 후 **새 작업 복사본**에서 `run_followup.py --study replication`, `--study termination` 순서로 실행함
기존 CSV 행이 있으면 재호출하지 않음
비공개 키와 `.env`는 저장소에 포함하지 않음
모델의 생성 확률과 시점 차이로 동일 응답은 기대할 수 없으며, 시나리오 4개·모델 1개의 관찰을 일반화하지 않음
