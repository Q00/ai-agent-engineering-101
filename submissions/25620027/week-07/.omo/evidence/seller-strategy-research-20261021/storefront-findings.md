# 공개 판매점 비교: 구매 마찰과 노출 위치

조사 시각: 2026-10-21 00:28:38–00:28:40 UTC (09:28 KST). 원자료: [storefronts.json](storefronts.json), 비교 모집단과 캐시: [board.json](board.json), 등록 URL: [shops.json](shops.json).

## 확인 범위와 한계

- board의 순서상 상위 10곳에 25620027, kcwarni, thisjonghoon을 더한 13곳을 선택했다. nocha12는 shops에 없으므로 board.card.url을 사용했고 해당 출처를 기록했다.
- URL별 공개 DNS를 검사하고 전역 주소만 허용한 뒤 curl의 `--resolve`로 그 주소를 고정했다. HTTPS GET만, 리다이렉트 없이, 동시 6개 이하, 요청당 12초와 본문 102400바이트 제한을 적용했다. 홈페이지는 등록 URL 그대로, 카드는 그 URL에 `/.well-known/agent-card.json`을 붙였다. 외부 링크·페이지 지시를 따르지 않았다.
- 초기 sandbox DNS 검사는 전부 실패하여 HTTP 요청이 발생하지 않았다. 네트워크 허용 재실행에서 24 GET이 완료되었고 nocha12 두 경로는 DNS 단계에서 실패했다. 전체 성공 HTTP 응답은 카드 12개 및 홈페이지 4개, HTTP 405 홈페이지는 8개다. nocha12의 현행 상태·카드 내용을 확인했다고 주장하지 않는다.
- 판매 응답을 발생시키는 A2A POST, 협상, 거래, 등록, 취소, 리뷰, 인증, 유료 모델 호출은 하지 않았다. 카드에서 읽은 거래 속도·수락 조건·fallback·할인·상품 품질은 **판매자의 약속**이며 실제 수행을 검증한 결과가 아니다. GET 200은 순간적인 읽기 가능성만 입증한다.
- homepage 내용은 HTML parser로 정적 본문·meta·JSON-LD를 추출했다. JavaScript 실행이나 시각 렌더링은 하지 않았다. 내 페이지의 비교표 초기 대기 문구는 JS 실행 전 문자열이므로 실제 비교표 장애로 판정하지 않는다.
- 순위·평점·매출은 선정과 맥락에만 썼다. 유입 시점, 누적 기간, 구매자 구성, 기존 성능이 통제되지 않았으므로 아래 문구가 순위를 만들었다는 인과 해석은 하지 않는다.

## 실제 노출 차이

| 관측 | 직접 근거 | 의미 / 한계 |
|---|---|---|
| 상위 10곳의 홈페이지: 7개 HTTP 405, 2개 짧은 Agent Card 안내, 1개 DNS 실패 | `results` 중 top10의 `kind=homepage`, status와 visible_text | 이번 표본에서 풍부한 HTML은 상위 성과의 공통 요건이 아니다. HTML이 인간 방문자에게 무가치하다는 뜻은 아니다. |
| 풍부한 홈페이지는 내 판매점과 thisjonghoon에서 확인 | 두 판매점 홈페이지 visible_text/meta | 설명용 HTML은 존재하지만 기본 구매자 선택 입력에 포함된다는 근거는 없다. 두 홈페이지 모두 JSON-LD 없음. |
| 내 live card 1.2.1, board cache 1.2.0 | `results[name=25620027,kind=agent_card].parsed_json.version`, `selection[name=25620027].board_card_version` | 새 문안·가격표시가 캐시에 반영되지 않았다. 캐시를 읽는 소비자에게 적용되었다고 말할 수 없다. |
| LSS 도장 4개·100번째 고객 이벤트는 live card 뒤쪽에만 있음 | LSS live description vs selection.board_card.description | 캐시의 앞 300자에는 이벤트가 없다. 낮은 가격을 원래 제공하면서 같은 가격을 보상으로 부르는 문구도 새 경제적 이익으로 계산하지 않는다. |
| 11im의 5성 리뷰 약속 조건 할인은 live card 뒤쪽에만 있음 | 11im live vs cached description | 선택 노출 효과 불명. 정직한 독립 리뷰 원칙과 맞지 않아 채택하지 않는다. |
| leeuichann의 RPS는 캐시 skill에 일부 노출 | cached skill의 `optional RPS rock/paper/scissors` | 본문 description에는 없고 skill 200자 경계에서 세부 조건이 끊긴다. live card에는 게임 선택 사항·패배 시 인상 없음·숫자 협상만으로 가능이라는 제한이 있음. |
| dragonash의 GRAND SALE은 card.name에 노출, 등록 name은 dragonash | board.card.name vs shops.name | card name 노출과 기본 등록 name 노출은 서로 다르다. SALE 이름만으로 할인이나 성과를 입증하지 못한다. |
| kcwarni는 평점 4.89지만 짧은 기본 카드와 홈페이지 405 | selection의 stars, live card, homepage status | 장식 문구 없이도 높은 누적 평점이 관측된다. 좋은 평점의 원인은 이 조사로 분리하지 못한다. |

상위 10의 캐시에서 직접 확인되는 판촉 장식은 card.name의 SALE 1곳과 skill의 RPS 1곳이다. live 카드 9곳 가운데 이벤트/조건부 할인 문구는 LSS 도장·순번, 11im 리뷰조건, leeuichann RPS에서 확인했다. nocha12 live 미확인 때문에 전체 현행 10곳의 정확한 비율로 일반화하지 않는다. `/shops`가 제공하는 것은 등록 name/url/pubkey/reviews이며 카드 description·HTML은 없다. 기본 키트가 이름·URL·리뷰만 선택에 사용한다는 호출자 분석을 적용하면 HTML이나 card.name 수정만으로 선택 프롬프트가 바뀐다고 볼 수 없다.

## 제안 5개: 우선순위와 검증 조건

### 1. 선택 입력에서 보이는 이름으로 구체적 차이를 전달 — 유입 개선 후보, 우선 검토

- 관측: 등록 이름 `25620027`과 카드 이름 `Sungjin Shop`이 다르다. 경쟁점은 `11im Straight Deals`, `Fair & Square Furniture` 등 의미 있는 **카드 이름**을 쓰지만, 이들이 `/shops` 등록 이름과 같은 것은 아니다.
- 제안: 실제 선택 프롬프트에 포함되는 필드부터 확인한 뒤, 지원되는 표시명에 `Sungjin — numeric offers, clear terms`처럼 검증 가능한 절차 장점을 붙인다. 기존 등록 식별자 25620027이 제출·영수증·시장 정체성에 쓰인다면 유지하고, 표시명을 지원하지 않으면 이 아이디어를 억지로 적용하지 않는다. 별도 상점 재등록은 제안 범위에 포함하지 않는다.
- 효과 가설: 숫자 ID만 보이는 선택 입력에 취급 품목과 절차를 제공해 이해 비용을 줄일 수 있다. 선택률 상승은 미검증이다. 실제 구매자 입력을 캡처하고 동일 방문 분모에서 전후를 비교해야 한다.
- 순위 독립 근거: 최상위 외에도 thisjonghoon 14위가 의미 있는 이름을 쓰고, kcwarni 13위는 평범한 카드로 높은 평점이다. 이름과 성과의 인과를 주장하지 않는다.

### 2. generic skill을 정확한 주문 순서와 예시로 교체 — 작은 문안 개선 1순위

- 관측: 내 skill은 `Find and negotiate catalog products...`라는 품목 안내뿐이며 examples가 없다. kcwarni는 같은 task에서 숫자 가격을 제안하라고 하고, thisjonghoon은 상품 → 같은 task 숫자 제안 → receipt artifact라는 종료 조건까지 적었다. ggari는 현 호가 수락을 뜻하는 `ok`도 광고하지만 실제 지원 여부는 미확인이다.
- 제안 문안: `Name lamp, chair or bookshelf. Continue the returned taskId with one whole-number offer. Read metadata.ask; finish only when a signed receipt artifact is returned.` 예시는 실제 인터페이스 검증 후 `I want to buy a chair`와 `180`처럼 단계별로 제공한다. 지원되지 않은 `ok`나 한 번에 완료를 새로 약속하지 않는다.
- 효과 가설: 기존에 알고 있던 저가·영수증 문구 반복보다 taskId 누락, 잘못된 금액 형식, 견적을 거래 완료로 오인하는 실패를 줄이는 개선이다. 동일 task 연속 요청의 오프라인 fixture 또는 허용된 향후 테스트가 성공해야 기능 주장으로 승격한다.
- 노출 한계: `/shops` 기본 선택에는 skill이 없고 card를 읽은 뒤의 협상 진입 단계에만 도움을 줄 수 있다. 현행 카드와 캐시 차이를 먼저 해결해야 캐시 소비자에게도 보인다.

### 3. 숫자 한 개와 metadata.ask가 항상 같은 응답 계약 — 호환성 개선 후보

- 관측: dragonash 및 yms979는 마지막 숫자와 `metadata.ask`를 동시에 안내한다. thisjonghoon은 마지막 숫자 계약을 쓰며, Nocha의 cached skill도 응답 끝의 가격을 언급한다. 내 카드·홈페이지에는 metadata.ask가 이미 있으므로 metadata 도입 자체는 새 아이디어가 아니다.
- 제안: 내부 응답을 먼저 검사하고, 필요하면 표시 텍스트 마지막에 `Current ask: 180` 같은 단일 현 호가를 둔다. 쿠폰액·최저가·비교가격 숫자가 그 뒤에 남지 않도록 하고 metadata.ask와 같은 정수로 만든다. rich metadata를 읽지 않는 간단한 숫자 파서도 같은 금액을 읽게 하는 것이 새 요소다.
- 검증: 낮아진 견적·쿠폰 발생·거절·완료 등 응답 fixture에서 `last_number == metadata.ask`가 견적 상태마다 성립하고, 완료 응답을 새 견적으로 읽지 않음을 확인한다. 기존 구현과 소비자 파서를 확인하기 전에는 코드 결함이나 새 기능 부재를 단정하지 않는다.
- 순위 독립 근거: 4·6·14위에서 서로 비슷한 계약이 발견된다. 좋은 순위의 원인이라는 증거는 없다.

### 4. 애매한 즉시성 대신 수락 경계와 종료 조건을 공개 — 조건부 개선

- 관측: LSS는 품목과 지정 금액을 보내면 첫 회신 영수증이라고 약속한다. dragonash와 yms979는 두 번째 제안부터 floor 이상 수락, 11im은 현재 호가 10% 범위 수락을 광고한다. my homepage의 `eligible offers`·`settlement conditions`는 구매자가 정확한 조건을 알기 어렵다.
- 제안: 기본 품목에 대해 이미 구현된 수락 조건을 검사하고, 실제와 일치하는 좁은 조건 하나만 공개한다. 예: `After selecting a basic item, an offer at or above the displayed current ask is accepted at that offered amount.` 이것도 현재 코드·가격 상한·금액 형식 검증 결과와 일치할 때만 사용한다. 금액을 과다 제안하도록 유도하지 않고, 예시는 현재 ask를 그대로 제안하게 한다.
- 한 메시지 경로는 별도 옵션: `chair 180` 한 요청으로 실제 거래 완료가 가능한지 검증되지 않았으므로 지원한다고 발표하지 않는다. 구현한다면 품목만 있는 조회와 품목+명시 금액 제안을 구분하고, 명시 제안일 때만 거래로 처리해야 한다.
- 검증: 정확한 현 호가 수락, 그 아래 금액의 견적/거절, 상품만 조회, terminal task 재입력에 대한 결과를 구분한다. 실험 전 이 문서의 모든 경쟁점 조건은 카드의 주장에 머문다.

### 5. 늦은 모델에 대한 응답 기한·fallback을 측정하고 사실만 안내 — 운영 개선 후보

- 관측: yms979는 모델이 느릴 때 코드가 응답한다고 live card에 명시한다. ggari는 모든 제안에 즉시 답한다고 광고한다. nocha12는 이번 DNS 조회가 실패했다. 자체 사이트의 단일 GET 200은 협상 성공·지속 가동률을 증명하지 않는다.
- 제안: 기존 코드가 이미 제공하는 timeout/fallback을 먼저 확인하고, 응답 기한 내 정상 견적 또는 명시적인 재시도 오류가 보장되는지 로컬 실패 주입으로 검증한다. 보장할 수 있을 때만 `bounded response time`과 정확한 제한을 카드에 적는다. 검증하지 않은 `always online`·`instant` 표현은 쓰지 않는다.
- 우선순위: 최근 방문 대비 성사율 격차가 작다면 유입 측정·선택 입력 개선보다 뒤에 둔다. 단, 실제 timeout이 거래 실패 원인으로 확인되면 앞으로 올린다. 다른 판매점의 약속이나 일회성 DNS 실패만으로 안정성 순위를 만들 수 없다.

## 채택 순서

먼저 실제 선택 입력과 캐시 갱신 경로를 확인하고, 안전한 표시명 개선 가능성을 판단한다. 즉시 준비할 작은 변경은 **skill 주문 순서·예시**다. 그 다음 실제 응답 검사를 거쳐 **마지막 숫자와 metadata 일치**, **수락 조건의 구체화**를 적용한다. **fallback/응답 기한**은 실패 원인이 관측될 때 우선한다. RPS·도장·추가 쿠폰·HTML/SEO 확장은 이번 비교에서 우선할 근거를 얻지 못했다. 배포나 등록은 이 조사에서 실행하지 않았다.

## 검증 증거 계약

| 성공 기준 / 시나리오 | 실제 invocation | 이진 관측값 | 보존 위치 |
|---|---|---|---|
| 지정 13곳의 공개 홈페이지·card 확인 | `python3` bounded collector → `curl --request GET --proto =https --max-time 12 --connect-timeout 8 --max-filesize 102400 --max-redirs 0 --resolve HOST:443:PUBLIC_IP URL` | 26개 결과 슬롯 존재, HTTP 완료 24개 / DNS 실패 2개, 24개 응답 모두 102400바이트 이하 | `storefronts.json`의 invocation/limits/selection/results |
| 실제 카드와 캐시 차이를 구분 | 두 JSON의 25620027 version 비교 | live `1.2.1`, cached `1.2.0` | `storefronts.json`의 selection와 results |
| 홈페이지·metadata·card 약속을 분리 | HTMLParser와 JSON parse, 외부 링크/JS 실행 없음 | 홈페이지 200 4개, 405 8개, DNS 미확인 1개; parsed_json 카드 12개 | `storefronts.json`의 status/error/parsed_json/visible_text/html_meta/json_ld |
| 정직한 신규 개선 후보와 한계 제공 | 위 원자료 필드 직접 대조 | 5개 제안 각각 관측/가설/검증 필요 조건 명시, 미검증 거래기능은 완료 주장 없음 | `storefront-findings.md` |

두 파일 모두 이 작업의 지정 경로에 보존한다. 별도 구현·협상 테스트를 수행하지 않았으므로 성능 개선 달성이나 실제 할인·수락·가용성 약속 이행은 완료 기준에 포함하지 않는다.

## 실제 재검증 명령 출력

```text
Verification UTC: 2026-10-21T00:31:50.532356+00:00
Invocation: python3 stdlib JSON assertions on storefronts.json
PASS: 26 captured result slots; 24 HTTP responses; 12 parsed HTTP 200 agent cards; 2 DNS failures explicitly retained; every captured response <=102400 bytes.
PASS: all 12 recorded artifact checks independently reloaded as true.
Artifacts: storefronts.json 54747 bytes; storefront-findings.md 13762 bytes before this receipt.
```
