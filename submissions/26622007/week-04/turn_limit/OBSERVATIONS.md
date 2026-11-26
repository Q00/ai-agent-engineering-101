# 원문에서 확인한 사례

## 8턴 이후에 가능한 합의에 도달

`structured-01`, 교재(reserve=40, budget=40)는 판매자가 60→55→50→45→40으로
제안을 낮췄다. 8턴에서는 아직 45라서 `open`, 10턴에 40을 제시하고 구매자가
11턴에 수락해 `deal=40`이 됐다. 이 대화에서는 턴 제한 제거가 정답 합의를 가능하게 했다.

- [원문 JSONL](../logs/unlimited-deepseek-20260922-structured-01.jsonl), 85–135행
- [대화 및 파싱 로그](../logs/unlimited-deepseek-20260922-structured-01.txt)

## 결과상 성공이어도 역할 행동은 별도 확인 필요

`tagged-02`, 탁상등(reserve=30, budget=45)은 13턴에 `deal=45`로 정답 처리된다.
하지만 판매자가 “I can offer …”로 가격을 올리고 구매자가 “could you come up”으로
더 높은 가격을 요구한다. 역할이 뒤바뀐 행동이 보여도 최종 가격은 양측 한도 안이다.
따라서 과제의 `correct`는 가격과 거래 가능성 기준이며 역할 수행의 질을 모두 평가하지 않는다.

- [원문 JSONL](../logs/unlimited-deepseek-20260922-tagged-02.jsonl), 38–122행

## 대화의 합의 금액과 프로토콜 기록 금액의 차이

`tagged-02`, 교재(reserve=40, budget=40)는 구매자가 35를 propose한 뒤
판매자가 `(reject-proposal)`에서 40을 요구했다. reject의 가격은 제안 상태에
등록되지 않는다. 구매자의 “Okay, I accept 40”은 판매자의 등록된 제안이 없어
형식 오류이고, 다음 판매자의 수락은 마지막 등록 구매자 제안 35에 연결된다.
결과는 `deal=35, violation=1`이다. 실제 대화는 40에 합의했으므로 판매자가
35를 명시적으로 수락했다고 해석하면 안 된다. 이 오류는 턴 제한 제거로 해결되지 않는다.

- [원문 JSONL](../logs/unlimited-deepseek-20260922-tagged-02.jsonl), 129–159행

## 81턴 동안 포기 문장을 반복해도 프로토콜상 미종료

`tagged-03`, 탁상등은 81턴 모두 선두 화행 태그 검증에 실패하고 180.999초에서
관측 중단됐다. 후반의 `\boxed{(refuse) Understood, the negotiation is over and
there is no deal.}`처럼 자연어와 내부 태그는 포기를 뜻하지만, 메시지 첫머리가
`(refuse)`가 아니므로 강의의 선두 태그 파서가 인정하지 않는다. reader 호출은 0회다.
파서를 느슨하게 바꾸거나 모델 출력을 보정하지 않고 원래 실험 규칙의 결과로 남겼다.

- [원문 JSONL](../logs/unlimited-deepseek-20260922-tagged-03.jsonl)
- [대화 및 파싱 로그](../logs/unlimited-deepseek-20260922-tagged-03.txt)

이 문서는 원문 관찰 기록이다. 독립 재실행 사이의 정답률 차이를 턴 제한 하나의 효과로
단정하지 않으며, 같은 대화의 8턴 prefix 비교는 `COMPARISON.md`에 별도로 둔다.
