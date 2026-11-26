# Week 06 — Codex buyer와 seller의 A2A 거래

## 1. 하네스, 모델, 실행 명령

seller와 buyer 모두 Codex CLI `0.162.0-alpha.17.2`를 하네스로 사용한다. 모델은 `gpt-5.6-luna`, reasoning effort는 `low`다. `codex_harness.py`는 매 판단마다 임시 빈 폴더를 만들고 `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--sandbox read-only`로 Codex를 실행한다. 따라서 저장소의 `AGENTS.md`나 사용자의 플러그인이 거래 판단에 섞이지 않는다. API 키를 코드나 파일에 저장하지 않고 기존 Codex 로그인을 사용한다.

실제 비대화형 명령은 다음과 같다.

```bash
codex exec --skip-git-repo-check --ephemeral --ignore-user-config \
  --ignore-rules --sandbox read-only --model gpt-5.6-luna \
  --config 'model_reasoning_effort="low"' \
  --output-schema decision.schema.json --output-last-message <temp>/answer.json \
  --cd <empty-temp-directory> -
```

trade test가 실행하는 명령은 `agent.json`에 기록했다.

```bash
python seller_agent.py --port {port}
python buyer_agent.py --url {url} --item {item} --budget {budget}
```

## 2. seller와 buyer의 판단 방법

seller는 새 task에서 품목과 하한가, 구매자의 문장을 프롬프트로 주고 Codex에게 최초 호가를 결정하게 한다. 후속 메시지에서는 현재 호가, 지금까지 받은 offer, 하한가를 함께 주고 `counter`, `accept`, `reject` 중 하나를 선택하게 한다. buyer는 Agent Card의 이름과 skill을 먼저 읽은 뒤 품목, 예산, seller의 최신 메시지, 협상 이력을 Codex에 전달한다. Codex는 `offer` 또는 `walk`를 결정한다. 규칙 기반 seller가 최초 호가를 reserve의 1.3배로 정한다는 관찰만 buyer 프롬프트에 주고, buyer가 `round(ask / 1.3)`을 첫 제안 후보로 판단하게 했다.

모델의 답은 자유 문장이 아니라 JSON Schema로 제한한다. 일반 거래 답은 `action`, `price`, `message` 세 필드를 반드시 가진다. 카탈로그와 가게 선택은 별도 schema의 `item_id`, `reason`을 사용한다. 가격과 행동의 의미는 A2A가 정하지 않으므로 이 구조를 seller와 buyer가 따로 합의한 셈이다.

하한가와 예산은 프롬프트와 코드 양쪽에서 지킨다. seller는 모델이 하한가보다 낮은 counter를 내면 `max(reserve, proposed)`로 올리고, 하한가 미만 offer에 대한 `accept`는 실행하지 않는다. buyer는 모델이 고른 가격을 `min(budget, offer)`로 제한한다. 가격 판단 자체는 Codex가 하지만 금전 한도 위반은 코드가 마지막에 차단한다.

## 3. 실패와 수정 과정

처음에는 수업의 규칙 기반 `seller_agent.py`만 있었고 buyer, `agent.json`, 공식 결과와 보고서가 없었다. 제출 구조를 처음 확인했을 때 checker는 `agent.json missing`, `test-report.json missing`, `logs/trade-test.log missing`, `REPORT.md missing` 네 가지를 보고했다. 이후 Codex 하네스 adapter와 buyer/seller를 구현하고 공식 trade test를 실행해 21개 실행을 모두 통과했다.

Challenge까지 구현한 뒤의 preflight에서도 보고서를 아직 만들지 않았기 때문에 `logs/preflight-failure.log`에 다음 실패가 남았다.

```text
FAIL  REPORT.md missing
1 check(s) failed
```

이 실패 뒤 지금 문서를 추가했다. 구현 후 buyer 프롬프트를 lamp 전용 예시에서 `ask / 1.3`으로 reserve를 추정하는 일반 규칙으로 바꾸어 chair와 bookshelf에도 같은 하네스를 사용할 수 있게 했다. 그 변경 뒤 공식 trade test를 다시 실행하여 결과 파일과 로그가 최종 코드에서 생성됐는지 확인한다.

## 4. A2A가 정한 것과 따로 맞춘 것

A2A는 상대 내부의 모델과 도구를 몰라도 Agent Card를 읽고 JSON-RPC 주소를 찾는 방법, `A2A-Version: 1.0` 헤더, 같은 협상을 `taskId`로 이어 가는 방법, `INPUT_REQUIRED`와 `COMPLETED` 같은 상태, 마지막 영수증을 artifact의 data Part로 전달하는 방법을 정해 주었다. 반면 A2A는 가격을 문장 어디에 쓰는지, 숫자 `100`이 새 offer인지 과거 가격의 인용인지, 어떤 표현이 수락인지, 몇 번 낮은 offer를 받으면 포기할지 정하지 않는다. 이번 구현에서는 seller 상태 메시지의 마지막 숫자를 current ask로 쓰고, buyer 메시지는 숫자 하나로 보내며, 성공한 거래는 `receipt` artifact의 숫자 `price`로 확인한다고 별도로 맞췄다.

## 5. 필수 Challenge 1과 2

Challenge 1은 생성 스크립트와 함께 2,003개 상품의 `catalog.json`을 커밋했다. 전체 카탈로그를 매 turn 프롬프트에 복사하지 않는다. `catalog_search.py`가 토큰, 동의어, 숫자 일치로 후보 6개만 찾고 Codex가 그 후보에서 최종 상품을 선택한다. `logs/challenge-1/seller.log`에는 질의 후보가 `featured-bookshelf` 등으로 줄고 Codex가 `featured-bookshelf`를 선택한 기록이 있다. client 로그에서는 “wooden”, “40 books”, “not taller than my desk”라는 설명만으로 `Compact Oak Bookshelf`를 찾아 reserve 260 아래로 내리지 않고 거래를 완료했다.

Challenge 2의 `multi_shop_buyer.py`는 약 20개 URL을 동시에 조회해 Agent Card directory를 만든다. 품목별로 카드의 이름, 설명, skill을 토큰 검색해 후보 다섯 곳으로 줄이고 Codex가 실제 가게를 고른다. 각 협상은 독립 subprocess와 독립 task ID를 사용하고 세 거래를 thread pool로 동시에 실행한다. 완료 후 receipt만 모아 품목 중복, 누락, 개별 한도와 총예산을 기계적으로 검사한다. `logs/challenge-2/result.json`에는 20개 카드 중 lamp, chair, bookshelf 전문점을 골라 세 품목을 한 번씩 샀고, 총 570 예산 중 540을 사용한 결과가 기록되어 있다.

Challenge 3과 공개 URL 배포는 선택 항목이므로 이번 제출 범위에서는 수행하지 않았다.
