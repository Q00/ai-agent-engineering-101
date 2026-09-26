# Week 04 보강: 발화 판독, 종료, 제안 상태, 반복 순서

이 보강은 [과제 명세](../../../../weeks/week-04/README.md)의 핵심 질문, 즉 **같은 buyer·seller 협상에서 메시지 형식과 판독 방식이 무엇을 바꾸는가**를 더 정확히 해석하기 위한 것이다. 기존 [본 실험 보고서](../REPORT.md)와 [36개 결과](../results.csv)는 그대로 둔다. 추가 모델 실험은 [사전 계획](PLAN.md)을 먼저 커밋한 후 수집했고, 연구별 CSV와 원본 콘솔 로그를 분리했다. 세 형식의 역할 프롬프트·모델·온도·턴 상한은 각 연구 안에서 같다. 형식 문단과 reader만 조건마다 다르다.
`correct`는 거래 가능 시 양쪽 한도 내 합의, 거래 불가능 시 명시적 `no_deal`이고 `open`은 실패다. `reader_calls`는 협상 에이전트 호출 외의 판독 모델 호출 수다. 추가 72개 에피소드에 실행 중단은 없었다.

## 1. reader가 뜻을 읽었는지 검사

기존 `free`와 `tagged`의 **175개 발화 전부**를 원문 기준으로 주석화했다([추출본](audit_input.csv), [주석](audit_labels.csv), [감사 코드](audit.py)). 거절 뒤 새 가격을 제시하면 주된 행위를 `propose`로 정하고, 두 해석이 가능한 문장은 `ambiguous`로 남겼다. 이것은 Codex 단일 주석자의 규칙 기반 판정이며 독립된 사람의 정답 표본은 아니다. 따라서 아래 수치는 객관적 정확도 추정이 아니라 재검토 가능한 불일치 기록이다.

| 조건 | 원문 발화 | 명확한 판정 | 모호 | 행위 불일치 | 비교 가능한 제안 가격 중 불일치 |
|---|---:|---:|---:|---:|---:|
| `free` | 90 | 88 | 2 | 1 | 0/64 |
| `tagged` | 85 | 85 | 0 | 0 | 0/48 |

`free`의 불일치 1건은 [free-01의 textbook 6턴](../logs/free-01.txt#L51-L54)이다. seller는 35를 거절하고 **50을 새로 제안**했지만 reader는 `reject-proposal`로 기록했다. 이 에피소드는 마지막 8턴에도 거래 없이 `open`이므로, [주석을 대입한 24개 에피소드 재생](audit.py)에서 최종 결과가 바뀐 것은 **0건**이다. 따라서 “오독이 성과를 낮췄다”는 인과 주장은 할 수 없다. 기존 `format_errors=0` 역시 문법상 파싱 성공만 뜻하며, 이런 의미 차이는 포함하지 않는다. `structured`의 기존 94개 메시지는 자연어 의도가 없어 별도 스키마 검사만 했고, 무효/파서 불일치가 0개였다. 이 결과도 구조화 메시지가 옳은 결정을 한다는 뜻은 아니다.

## 2. 같은 과제의 반복을 늘리고 조건 순서를 섞음

[추가 실행기](run_followup.py)는 원본과 같은 시나리오 4개, `gpt-5.6-luna`, temperature `0.7`, `reasoning_effort=none`, `max_completion_tokens=512`, 최대 8턴을 사용했다. `replication`은 역할·형식·reader 문구를 바꾸지 않고 조건별 3회씩 추가했다. 세 블록의 조건 순서를 `structured → tagged → free`, `tagged → free → structured`, `free → structured → tagged`로 고정했다. 추가 [36개 CSV](replication.csv)와 [9개 원본 로그](../logs/replication-10.txt)를 따로 보존했다. 원본 36개는 `free` 세 run, `tagged` 세 run, `structured` 세 run 순서였으므로, **합친 72개 전체가 완전한 무작위·균형 순서는 아니다**.

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

같은 프롬프트를 더 실행해도 거래 불가능한 `keyboard`와 `laptop`은 세 형식에서 각각 **0/6 correct**, 모두 `open`이었다. 가능한 `bike`/`textbook`의 correct는 `free` 5/6·2/6, `tagged` 5/6·5/6, `structured` 2/6·2/6이다. 따라서 태그가 reader 호출을 줄이는 효과는 측정되지만, 이 작은 고정 시나리오 묶음만으로 형식의 일반적 성공률 순위를 확정할 수 없다.

## 3. 종료 행위는 공통 정책으로 별도 검사

원래 실험은 불가능한 거래에서 `refuse`가 없어 `no_deal=0`이었다. 이를 형식 문제로 혼동하지 않기 위해 [사전 계획의 동일한 deadline 지침](PLAN.md)을 buyer와 seller **모두**의 공통 프롬프트에 덧붙였다. 마지막 자기 차례에 수락 가능한 제안이 없으면 `refuse`하고, 수락 가능한 상대 제안은 역제안 대신 수락하도록 요청한다. 원본 과제의 비교 조건이나 원본 CSV는 수정하지 않았다. 별도 [36개 결과](termination.csv)와 [9개 로그](../logs/termination-19.txt)를 수집했다. 조건 순서는 블록마다 `tagged → structured → free`, `free → tagged → structured`, `structured → free → tagged`다.

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

세 조건 모두 `open=0`이고 거래 불가능한 경우에도 명시적으로 떠난 사례가 생겼다. 동시에 deadline 프롬프트를 줘도 **가능한 textbook 거래를 `no_deal`로 끝낸 경우**가 `tagged`와 `structured`에서 각 1건 있었다. 두 한도 위반은 원본 그대로 남겼다. [tagged run 23](../logs/termination-23.txt#L30-L38)에서는 buyer 예산 45인데 seller의 50 제안을 buyer가 수락했다. [structured run 20](../logs/termination-20.txt#L56-L61)에서는 buyer 예산 70인데 seller의 90 제안을 buyer가 JSON `accept-proposal`로 수락했다. 둘 다 파싱은 성공했다. 즉 **형식상 명확한 수락도 비공개 한도 준수를 보장하지 않는다**. [structured run 25](../logs/termination-25.txt#L69-L76)의 `content:null`은 필수 `content.price`가 없어 파서가 읽지 못한 1건으로, 다음 발화가 `refuse`여서 이 에피소드는 `no_deal`로 끝났다.

이 연구는 모든 조건에 같은 행동 지침을 적용해 종료 실패 양상을 살핀 **탐색적 후속 실험**이다. 원본과 생성 시점·프롬프트가 달라 원본 대비 변화량을 정책의 인과 효과나 형식 자체의 효과로 단정할 수 없다. 특히 턴 제한 도달도 `open`으로 기록되는 기존 점수 규칙을 유지했다.

## 4. `accept-proposal`의 대상 제안을 명확히 함

원본 harness는 상대가 과거에 마지막으로 제시한 가격을 기억한다. [별도 재생기](protocol_audit.py)는 더 엄격한 **대안 규칙**을 시험한다: 새 제안이 나오면 이전 제안은 만료되고, 거절된 제안도 닫힌다. 닫힌 제안을 뒤늦게 수락하는 행위는 거래가 아니다. 이는 과제의 간단한 “상대의 마지막 가격” 규칙이 틀렸다는 뜻도, FIPA가 이 정확한 상태기를 요구한다는 뜻도 아니다. [합성 사례 테스트](test_protocol_audit.py)는 과거 가격 수락이 두 규칙에서 다르게 판정됨을 보여 준다.

실제 원본·반복·종료 연구의 **108개 에피소드 전부**를 두 규칙으로 재생한 결과, 최종 결과·가격이 달라진 에피소드는 **0개**, 엄격한 규칙에서 유효하지 않은 수락도 **0개**였다. 새 제안이나 거절로 만료된 제안은 연구별 158·150·76개였지만 이는 정상적인 역제안 과정도 세는 값이므로 오류 개수로 읽으면 안 된다. 현재 관측 결과를 고치기보다 프로토콜 의미가 달라질 수 있는 경계 사례를 명시한 것이다.

## 검증·재현

[분석기](analyze.py)는 원본·반복·종료 연구의 조건별/시나리오별 표를 CSV에서 다시 만든다. [검증기](verify_followup.py)는 추가 CSV 72행을 18개 원본 로그와 대조하고 실행 순서·시나리오/지침 해시·파서/reader 기록·한도 위반을 검사한다. 아래 명령은 API 호출 없이 이미 기록된 데이터를 확인한다(저장소 루트 기준).

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

새 API 재실험은 `requirements.txt`를 설치하고 비공개 키를 환경변수로 설정한 **새 작업 복사본**에서 `run_followup.py --study replication`과 `--study termination`을 차례로 실행한다. 실행기는 기존 CSV 행이 있으면 재호출하지 않는다. 비공개 키와 `.env`는 저장소에 포함하지 않았다. 모델의 생성 확률과 시점 변화 때문에 동일한 응답은 기대할 수 없으며, 네 개 고정 시나리오와 한 모델의 관찰을 일반화하지 않는다.
