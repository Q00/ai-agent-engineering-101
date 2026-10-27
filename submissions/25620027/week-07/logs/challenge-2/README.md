# 챌린지2: 직접 관측한 사칭 지시와 실제 회피

`observed-response.log`는 본인 `logs/market-kit.log:8483`의 원문 한 줄이다. 이 응답의 status.message에 시장 정책 사칭·현재 가격 수락·5점 지시가 있다. 외부 문구는 실행 지침으로 사용하지 않는다. 상대 사람의 신원·고의나 모든 손해의 원인은 단정하지 않는다.

기존 직접 관측 기억을 보존하고 `market_a2a/market_buyer.py`·`market_decision.py`에서 해당 이름의 도구 선택을 막는다. 협상에는 상태·호가만 전달한다. 표본·탐지 범위와 과거117기억의 의미는 `../../.omo/evidence/security-review/incident-report.md`에 있다.

`before.json`은 기존 Mac 로그의 R2 고유주문68·조회확인68·bright-living65건이다. `after.json`은 VPS 원문 스냅샷의 R2 고유주문533·조회확인531·bright-living0건, 부적격/권한 사칭을 명시한 실제 선택 이유515건이다. 첫 두 선택 이유의 원본 행 위치도 함께 저장했다. 카드 조회 전의2건은 성공 회피로 집계하지 않았다. 최신 공개 후기만 보고 가게를 차단한 실험이나 임의 악성 예시를 실제 사건처럼 사용한 것이 아니다.

재현: 제출 디렉터리에서 `python .omo/evidence/final-submission-20261027/summarize_challenges.py`. 입력 해시·행 수를 결과에 보존한다. 원본 구매 로그는 `../../.omo/evidence/final-submission-20261027/vps-market-kit-20261027.log`다.
