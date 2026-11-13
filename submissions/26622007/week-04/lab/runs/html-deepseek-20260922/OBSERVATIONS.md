# HTML 참조 사례와 대조한 실제 관찰

## 대화에서는 40, 프로토콜에는 24로 남은 거래

`html-deepseek-20260922-free-02`, scenario 3(교재), reserve=40, budget=40.

| 턴 | 대화 요지 | 판독과 상태 |
|---|---|---|
| 1 | 구매자가 24 제안 | propose 24 → 구매자 마지막 제안=24 |
| 2 | 판매자가 24를 거절하며 최소 40이라고 말함 | reject-proposal, price 40 → 판매자 제안은 등록되지 않음 |
| 3 | 구매자가 40에 맞추겠다고 말함 | accept-proposal → 등록된 판매자 제안이 없어 format_errors |
| 4 | 판매자가 40을 수락한다고 말함 | accept-proposal → 구매자의 마지막 기록인 24로 deal |

프로그램의 결과는 price=24, violation=1, correct=0이다. 실제 마지막 발언은
"I accept your offer of 40. Let's finalize the deal."이다. 모델이 실제로 24에 팔았다고
단정하면 안 된다. 이 위반은 행위 분류와 제안 상태 갱신이 대화 의미를 따라가지 못한 결과다.
HTML의 탁상등 사례처럼, 판독 결과를 가격 상태에 반영한 뒤 그 상태로 거래를 종료했다.

근거: [원본 JSONL](../../../logs/html-deepseek-20260922-free-02.jsonl)의
73, 78, 82, 87, 91, 96, 100, 105행. 로그나 결과 가격을 사후에 고치지 않았다.

## 실제로 없던 제안을 수락하며 8턴 소진

`html-deepseek-20260922-tagged-01`, scenario 3(교재), reserve=40, budget=40.

구매자 첫 발언에 "The seller's last proposal is 30"이라는 문구와
"(accept-proposal) I accept your offer of 30 for the textbook."이 함께 등장했다.
판매자는 아직 발언하지 않았다. 첫 메시지는 앞에 태그가 없어 파싱에 실패했다.
뒤이어 두 에이전트가 30을 수락하는 발언을 반복했지만, 유효한 propose는 한 번도 없어
거래를 성립시키지 않았다. 결과는 open, turns=8, format_errors=8, reader_calls=0이다.

근거: [원본 콘솔](../../../logs/html-deepseek-20260922-tagged-01.txt)의 scenario 3 메시지와 parse_result.
이 사례는 실습에 없는 화행을 추가하거나 수락 문장에서 가격을 추정해 복구하지 않았다.

## 해석 범위

형식 오류에는 JSON/태그 오류뿐 아니라 상대의 기록된 제안이 없는 수락도 포함된다.
strict JSON Schema가 적용된 조건과 text 조건의 형식 오류율은 API 제약의 영향을 받는다.
각 조건 12개 에피소드의 관찰이며, 모델 전체의 협상 능력이나 형식의 일반적인 우열을
이 결과만으로 확정하지 않는다. 강의의 FIPA 비교표와 제출용 최종 해석은 별도 작성 대상이다.
