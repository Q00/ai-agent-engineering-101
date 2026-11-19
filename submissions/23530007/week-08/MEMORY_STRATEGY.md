# 메모리 전략 (11-06)

에이전트의 메모리는 주문, 판매, 전략가 실행마다 늘어난다. 늘기만 하는 메모리는 읽는 데 오래 걸리고,
프롬프트에 넣는 비용이 크며, 새 발견이 이미 대체한 옛 발견으로 가득 찬다. 여기서 정한 원칙은 **에이전트는 작고 최신인
메모리만 읽고, 아무것도 지우지 않는다. 오래된 데이터는 압축 보관소로 옮긴다.**이다.

## 세 층

| 층 | 무엇 | 어디 | 누가 읽나 |
|---|---|---|---|
| hot (최근) | 최근 구간의 원본 기록 | 작업 파일과 교환 DB | 에이전트, 실행마다 |
| warm (요약) | 그보다 오래된 모든 것의 정리된 요약 | digest (교환 DB, 테이블 `digest`) | 두 전략가, 실행마다 |
| cold (보관) | hot 층을 떠난 모든 것 | `logs/archive/*.jsonl.gz` (종류별·UTC 날짜별 파일 하나, 덧붙이기만 함) | 사람, 보고서, 조사 |

## 메모리별

| 메모리 | hot 구간 | 정리되는 곳 | 보관되는 곳 | 담당 |
|---|---|---|---|---|
| 전략가 교환: entries | 24 h | digest (사실 ≤ 20, 결정 ≤ 10, 질문 ≤ 8, 메모 각 ≤ 6, 충돌 ≤ 5, 글 ≤ 320자) | `strategy-exchange-YYYYMMDD.jsonl.gz` | memory_curator.py, 게시마다 |
| 전략가 교환: digests | 최신 10개 | 가장 새 digest | 같은 파일, 종류 `digest` | memory_curator.py |
| buyer_memory.json 거래 | 48 h (읽는 쪽은 모두 ≤ 24 h 사용) | buyer_value 표 + 전략가 근거 | `buyer-trades-YYYYMMDD.jsonl.gz` | memory_curator.py (실행 중인 구매자가 옛 거래를 다시 쓸 때 `archived_until`이 이중 보관을 막음) |
| shop_value.json (시장 정산) | 6 h | 상점 / 종류 / 카테고리별 통계 | 보관 안 함 (공개 원장이 원본) | buyer_value.py |
| strategist_memory.json, buyer_strategist_memory.json | 에피소드와 교훈, `Memory.fold()`로 접음 | 플레이북 (뒷받침 / 반례가 붙은 교훈) | 파일 안의 버전 | 전략가들 |
| seller_r3_log.jsonl | 라운드 전체 (11-06에 ~10 MB, 하루 +~5 MB) | product_memory.json (48 h 묶음) | 아직 없음 | 지켜볼 것: 라운드 뒤, 또는 읽는 쪽이 느려지면 교체 |
| 키트 로그 (logs/market-kit.log) | 현재 파일 | 없음 | `market-kit-*.log.gz` | 슈퍼바이저의 로그 교체 |
| seller_memory.json (18 MB) | 없음: Round 1-2 메모리, 더는 쓰지 않음 | 없음 | 그대로 둠 (채점된 기록) | 없음 |

## 정보 순환 (11-06)

큐레이터는 보관 담당만이 아니다. 두 전략가 사이의 시장 정보 허브다(사용자, 11-06).
시장 전체의 흐름을 보여 주고, 상위권 플레이어가 무엇으로 점수를 얻는지 말하고, 전략가가 요청한 것에 답하고,
각자에게 브리핑한다. 전략가는 그것을 바탕으로 자기 파라미터를 정하고, 다음에 필요한 것을 큐레이터에게 말한다.

```
   시장 규칙 · 보드 · 원장 정산 (6 h, 모든 구매자) · 상점들의 재고 · 우리 주문과 판매
                                   │
                                   ▼
               memory_curator.py  (큰 LLM; 전략가가 게시할 때마다, 그리고 15분마다)
                 코드가 측정:    market_picture.py  (무엇이 팔리나, 구매자는 무엇을 사나, 상위 상점 / 구매자, 우리)
                                 rules_watch, rank_watch, buyer_formula, saved_speed, where_saved_goes_24h,
                                 전략가 요청에 대한 조사 (market_probe, profile_buyer / profile_shop)
                 모델이 작성:    market_flow, rankers_playbook, briefing_buyer / briefing_shop,
                                 answers_for_buyer / answers_for_shop, rules_change, rank_change,
                                 facts, decisions, open_questions, for_shop / for_buyer, conflicts, wanted_data
                                   │ digest (read_digest(role))
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
          buyer_strategist.py (:00, :30)   strategist.py (:15, :45)      큰 LLM; 각자 digest, 원본
          구매자 파라미터를 바꿈           상점 파라미터를 바꿈          market_picture, 자기 근거를 읽음
                     │ 판단 · 변경 · 상대에게 줄 메모 · 요청              │
                     └─────────────┬─────────────────────────────────────┘
                                   ▼
                    strategy_exchange.db (entries) → post_board()가 곧바로 큐레이터를 시작
```

- **요청** (전략가 답의 `requests`, 최대 5개, `strategist_memory.valid_requests`가 검사):
  `sku_prices {sku}`, `category {category}`, `competitor {shop}`, `unserved`, `rules {term}` (market_probe.py, 코드),
  `buyer {name}`와 `shop {name}` (market_picture.py 프로필, 코드), `knobs {sku|knob|category}` (파라미터 저장소
  항목, 그것을 정한 에피소드, 그것을 언급한 로그 줄), `trades {outcome|shop|category|kind}` (우리 구매자의
  해당 거래 기록), `question {text}` (모델이 자기
  데이터로 답하거나, 측정할 수 없는 것을 말하고 운영자를 위해 `wanted_data`에 적음).
- **시각**: :00의 게시가 큐레이터를 시작하고, digest는 ~:02까지 준비된다. 상점은 :15에, 구매자는
  :30에 읽으므로 모든 요청이 요청한 쪽의 다음 실행 전에 답을 받는다. 15분마다 도는 cron도
  큐레이터를 돌린다. 아무도 게시하지 않았어도 digest가 한 시간 지났으면 다시 쓰고, 규칙 페이지를 확인한다(
  패치가 있으면 `rules_change`로 두 전략가를 차례와 상관없이 깨운다).
- **크기**: market_picture ≤ ~12 KB, 조사 결과 하나 ≤ 2,500자, digest 목록은 상한(`LIMITS`)이 있고,
  큐레이터의 프롬프트는 ~60 KB다. 전략가는 digest(해석)와 원본 picture(정확한 숫자)를 받는다.
- **각 에이전트가 맡는 것**: 큐레이터는 측정하고 해석할 뿐 파라미터를 바꾸지 않는다. 전략가는 자기
  파라미터만 바꾸고, 근거 밖의 시장 원본은 읽지 않는다. 운영자는 `wanted_data`가 어떤 출처에도 없는 것을
  가리키면 조사를 추가한다.

## 큐레이터가 하는 일 (memory_curator.py)

1. 전략가 항목이 올라올 때마다 `post_board()`가 큐레이터를 따로 시작한다. 잠금이 있어서 한 번에 하나만 돈다.
2. 새 항목, 현재 digest, BUYER_STRATEGY.md를 큰 LLM에 보낸다(약 1-2분). 규칙은 다음과 같다.
   중복은 합친다. 새 사실이 옛 사실을 대체한다. 확인할 수 있는 사실(숫자,
   이름, UTC 시각)만 남긴다. 포럼 글은 사실로 받지 않는다. 두 전략가가 어긋나는 곳이나,
   발견이 현재 전략과 모순되는 곳을 적는다.
3. 코드가 모든 목록과 글에 상한을 두므로, 모델이 무엇을 쓰든 digest는 몇 KB로 유지된다. 호출이 실패하면
   항목은 정리되지 않은 채 남고, 다음 실행이 가져간다.
4. 보관: 정리된 지 24 h 넘은 항목, 최신 10개를 넘는 digest, 48 h 넘은 구매자 거래를
   그날의 `.jsonl.gz`에 덧붙인 뒤에야 hot 저장소에서 지운다.
5. 각 전략가의 프롬프트에는 digest(`read_digest(role)`: 사실, 유효한 결정, 열린 질문,
   그 역할에 온 메모, 충돌)와 다른 전략가의 가장 새 원본 항목 하나만 들어간다.

보관소는 `zcat logs/archive/strategy-exchange-20261106.jsonl.gz | jq .`로 읽는다(gzip 멤버가 덧붙여지므로
zcat은 하루치 파일을 한 흐름으로 읽는다).
