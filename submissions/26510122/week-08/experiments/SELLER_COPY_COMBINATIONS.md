# Seller 소개 조합 실험 — 2026-11-03

**토큰 절약·네고 가능·명확한 조건을 함께 제시한 문구가 내부 첫 연락 선택에서 가장 유망했다.**
탐색 60회와 별도 검증 36회를 수행했으며, 모델 요청 96회가 모두 완료됐다.
주 지표는 소개를 읽은 바이어가 **어느 가게에 먼저 연락하는지**다.
협상 왕복이나 토큰 사용량 감소를 선발 조건으로 삼지 않았다.

선정한 문구는 로컬 `seller_r3.py`의 Agent Card에 그대로 반영했다.
실행 중인 시장 서버는 재시작하거나 변경하지 않았다.

> Brief quotes help save tokens. Negotiable prices; counteroffers welcome.
> Clear prices and checkout certificates. Stocked products.
> Numeric offers; signed receipts on agreement.

## 이전 실험에서 바꾼 상황

기존 실험은 이미 같은 호가를 아는 가게들 중 하나를 고르게 했다.
이번에는 **아직 확정 호가를 받지 않은 첫 문의 단계**로 바꿨다.
실제 바이어가 문의 전에 아는 카탈로그 정가·재고·상품 조건을 제공하고,
`quoted_price=null`, `quote_status=not_requested`로 명시했다.
따라서 이전 실험의 선택 횟수와 이번 수치를 합산하지 않는다.

가게의 재고·정가·등급·증명서 제공·협상 가능·보증·리뷰 조건은
한 시험 안에서 전부 동일하다. 소개만 그 사실을 다르게 강조한다.
후보 ID는 익명이며 모델에 조합 이름이나 실험상 정답을 알려주지 않았다.
바이어 목표는 유효한 구매 잉여와 정산 가능성이고, 토큰 절약을 별도로 지시하지 않았다.

## 1. 조합 탐색: 60회

공통 문구는 `Stocked products. Numeric offers; signed receipts on agreement.`다.
다음 요소의 유무를 조합해 8개를 만들고, 기존 소개 2개를 참조로 더했다.

| 요소 | 문구 |
| --- | --- |
| T: 토큰 | Brief quotes help save tokens. |
| N: 네고 | Negotiable prices; counteroffers welcome. |
| C: 조건 | Clear prices and checkout certificates. |

각 모델 설정에서 식품·비식품을 각각 10개 순환 순서로 제시했다.
한 상품 상황에서 모든 문구가 모든 위치에 정확히 한 번 등장한다.
모델별로 20회, 세 모델에서 총 60회다. 모델 간 사용자 프롬프트는 같다.

| 문구 | gpt-5.6-luna /20 | gpt-6-luna /20 | Claude sonnet /20 |
| --- | ---: | ---: | ---: |
| 공통 문구만 | 0 | 1 | 0 |
| C | 0 | 0 | 0 |
| N | 1 | 0 | 0 |
| N+C | 1 | 2 | 4 |
| T | 0 | 2 | 0 |
| T+C | 0 | 0 | 0 |
| T+N | 1 | 0 | 0 |
| **T+N+C** | **16** | **15** | **16** |
| 기존 토큰 절약 소개 | 0 | 0 | 0 |
| 기존 명확한 조건 소개 | 1 | 0 | 0 |

새 조합 중 T+N+C가 47/60회 선택됐다. 다음 단계에 이 조합 하나와
기존 두 참조 문구를 넣었다. 선발 규칙은 실행 전 코드에 고정했다:
모델 동일 가중 선택 수, 동률이면 선택한 모델 수, 짧은 문구, ID 순서다.
코드와 탐색 결과 및 고정한 shortlist는 최종 검증 호출 전에 커밋했다.
원문은 `../logs/copy-combo-shortlist-2026-11-03.json`에 있다.

## 2. 별도 검증: 36회

세 문구의 모든 쌍을 비교했다. 각 쌍은 식품·비식품에서 양쪽 순서로
각각 제시했다. 쌍/상품 상황의 두 순서는 같은 익명 ID를 사용하므로,
각 문구가 두 위치와 두 ID를 한 번씩 받는다.

탐색에 쓰지 않은 상품과 예산을 사용했다. 식품은 정가 1,600·예산 1,300,
남은 시간 10시간·6시간 내 사용, 비식품은 정가 2,400·예산 1,900이다.
여전히 문의 전이므로 확정 호가는 없다. 모델별 12회 중 각 문구는 8회 등장한다.

| 문구 | gpt-5.6-luna /8 | gpt-6-luna /8 | Claude sonnet /8 |
| --- | ---: | ---: | ---: |
| **새 조합 T+N+C** | **8** | **5** | **6** |
| 기존 토큰 절약 소개 | 3 | 4 | 3 |
| 기존 명확한 조건 소개 | 1 | 3 | 3 |

전체 쌍대 비교에서 새 조합은 24회 등장해 19회 선택됐다.
기존 토큰 절약 소개와 직접 비교하면 10/12회, 기존 조건 소개와는 9/12회다.
이는 **내부 선택 횟수**이며 실제 시장 방문률이나 구매 전환율이 아니다.

모델이 스스로 보고한 선호 강도는 세 설정 모두 약함 6회·동등함 6회였다.
강한 선호라는 응답은 없었다. 첫 번째 후보 선택도 설정별 7/12·10/12·10/12로
남아 있다. 순서를 균형화했어도 작은 표본과 모델의 동률 처리에 민감하다.
gpt-6-luna는 새 조합과 기존 조건 소개의 직접 비교에서 2:2로 동률이었다.
따라서 새 조합을 유망한 소개로 채택하되 모든 모델의 확정적 선호라고 부르지 않는다.

원문 이유에는 네고·역제안을 환영하면서 조건·증명서·영수증 절차도
명시한다는 설명이 반복됐다. 이는 모델의 사후 설명으로,
세 요소 각각의 독립적 인과 효과를 증명하지 않는다.
문구 길이와 강조 내용도 함께 달라진다.

## 재현과 기록

`seller_copy_combinations.py`가 문구 생성, 균형 배치, 익명 ID, 선발 규칙,
새 상황의 검증과 결과 집계를 담당한다. 각 모델은 격리된 새 CLI 세션에서
응답했고, 시장 주문·게시판·결제 엔드포인트는 호출하지 않았다.
Claude는 요청 별칭 `sonnet`을 사용한다. 실제 응답의 모델별 usage에는
`claude-sonnet-5`가 포함됐으며, CLI의 추가 Haiku usage를 별도 바이어로 세지 않는다.
모델 업체의 CLI 래퍼는 완전히 같지 않다.

모든 `logs/copy-combo-screen-*-2026-11-03/`와
`logs/copy-combo-confirm-*-2026-11-03/` 디렉터리는 manifest·원문 요청·응답·usage·결과를 보존한다.
최종 집계는 `../logs/copy-combo-confirmation-report-2026-11-03.json`이다.
이전 로그나 성공한 시행을 덮어쓰거나 유리한 결과만 다시 선택하지 않았다.

다음 명령을 모델 설정 세 개에 대해 실행한다. 출력 디렉터리는 새 경로여야 한다.

```sh
WEEK08_MODEL_TIMEOUT=90 .venv/bin/python experiments/seller_copy_combinations.py run \
  --stage screen --provider codex --model gpt-5.6-luna \
  --output logs/new-screen-gpt56 --workers 2
# 다른 두 설정: --provider codex --model gpt-6-luna / --provider claude --model sonnet

.venv/bin/python experiments/seller_copy_combinations.py shortlist \
  logs/new-screen-gpt56 logs/new-screen-gpt6 logs/new-screen-claude \
  --output logs/new-shortlist.json

WEEK08_MODEL_TIMEOUT=90 .venv/bin/python experiments/seller_copy_combinations.py run \
  --stage confirm --provider codex --model gpt-5.6-luna \
  --shortlist logs/new-shortlist.json --output logs/new-confirm-gpt56 --workers 2
# 같은 shortlist로 나머지 두 모델도 실행

.venv/bin/python experiments/seller_copy_combinations.py report \
  logs/new-confirm-gpt56 logs/new-confirm-gpt6 logs/new-confirm-claude \
  --output logs/new-confirmation-report.json
```

검증 기록 `../logs/copy-combo-validation-2026-11-03.log`에서 96회 완료,
모델 간 동일 프롬프트, 동일한 가게 조건, 위치·ID 노출 균형을 확인했다.
로컬 ASGI Agent Card가 선정 문구를 정확히 반환함도 확인했다.
기존 전략·통합 테스트 18개가 통과했다.

현재 우리 바이어의 선택 단계에는 Agent Card 소개가 들어가지 않는다.
이 실험은 소개를 실제로 보여줬을 때의 내부 선택 반응이다.
실제 시장에서 어떤 바이어가 소개를 읽고 방문하는지는 별도 관찰이 필요하다.
