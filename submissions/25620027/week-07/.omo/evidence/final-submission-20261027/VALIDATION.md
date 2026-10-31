# 최종 검수 기록

## 소스와 범위

- 교수 main: `9d349639623d6ede65022275ad2bdd48aa8fbabc`, 병합 후 공식 검사기 원문을 사용했다.
- 최종 실행 소스: `96405f5`. CLI 유료 요청도 기존 공유 SQLite 원장을 사용하도록 보완했다. 가격·후기 정책을 이번 제출 중 새로 바꾸지 않았다.
- 오프라인 336개 통과: `offline-fixed.log`. 변경한 Python 파일에 대한 strict basedpyright 0 errors/0 warnings와 Ruff 통과: `types-fixed.log`, `lint-fixed.log`.
- 첫 오프라인 검사의 날짜 의존 실패 2개와 타입/린트 진단 원문도 보존했다. 무료 행사 종료 전후를 명시적으로 검사하도록 수정했다.

## 실제 공식 검사 시도

1. 로컬 환경 변수 이름 불일치: `logs/final-validation-20261027/attempt-1/`.
2. 로컬 무료 라우터: 21/25. 일시 요청 오류 후 재시도를 소진했다. `logs/final-validation-20261027/attempt-2/` 및 `market-test-attempt-2-console.log`.
3. VPS 부트스트랩: 임시 파일시스템 noexec/캐시 권한 문제로 실패했다. `official-nano-console.log`, `official-nano-attempt-4-console.log`에 보존했다.
4. VPS OpenAI: 기존 nano snapshot이 API 404를 반환해 1/24로 실패했다. `official-nano-final-console.log`. OpenAI 공식 deprecations 페이지에서도 2026-10-23 종료를 확인했다. 유료 대체 모델을 임의 선택하지 않았다.
5. VPS 무료 경로: `official-free-vps-console.log`는 사용자 점심 중단 요청에 따라 검증 컨테이너를 중지한 원문이다. 완료·통과 결과로 세지 않는다.
6. 사용자 재개 후 VPS 무료 전체 검사: `official-free-vps-resumed-console.log`, 23/25 실패. 예산 방어 마지막 실행은 JSON 출력 없이 끝났고 후기 선택은 1/3만 확인돼 미통과했다. 응답 지연·429 원문은 `logs/final-validation-20261027/free-resumed-23of25/`에 보존했다.
7. 사용자 추가 승인 후 mini 최종 검사: `official-mini-final-console.log`. 소스 `2be0af7`, `AX_MODEL_PROVIDER=openai`, `AX_OPENAI_MODEL=gpt-4.1-mini-2025-04-14`, 기존 SQLite 공유 원장·추가 최대100시도다. 시작 시 원장27이어서 검증 프로세스 한도127을 설정했다. 다른 서비스의 유료 시도도 이 숫자를 소비하므로 검증이100회를 넘길 수 없다. 요청의 messages/tools UTF-8 JSON을16000바이트 이내, 출력450토큰으로 제한한다. 입력 토큰을 바이트 수+프레이밍2000으로 보수적으로 잡아도 공식 mini 요율(input $0.40/output $1.60 per1M)로100회 최대 약$0.792다. 상시 운영 모델·5000회 한도는 바꾸지 않았다. 결과 JSON은 원문을 복사하며 수동 편집하지 않는다.

최종 mini 검사는25/25 통과, 후기 선택도good/good/good 3/3이다. 제출 `test-report.json`과 루트 test 로그는 이 실행의 원문이다. mini 설정과 전송 전 입력 상한을 회귀 검증한 최종 오프라인 검사는338개 통과다. `offline-mini.log`, `types-mini.log`. [공식 요율](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

실제 검증78시도·78응답, 입력61402/출력4515토큰이며 표준 요율 환산$0.031785다. `validation-usage.log`는 seller-under-test와9개 buyer-under-test 로그의 OPENAI_ATTEMPT와 MODEL_USAGE를 중복 없이 집계했다. 현재 교수 구조 검사도전체 통과했다(`structure-final.log`).

실서비스와 분리한 검증 컨테이너에서 키트의 규칙 기반 상대와만 거래한다. 검증 영수증은 실제 시장 장부·후기로 제출하지 않는다. 검증용 개인키·환경 파일은 제출하거나 가져오지 않는다. 원문 로그의 공백·표현은 수정하지 않는다.

## 운영 체크포인트

`board-final.json`은 제출 준비 중 공개 R2 보드를 GET한 원문이다. 등록명 25620027의 shop/buyer 모두 owner SungJinho·alive true, 판매 3090건/5515방문·평점4.77, 구매467성사/636주문을 확인했다. 누적1200주문·2026-10-28 17:15 KST·유료 하루5000시도 상한을 유지한다. 기존 nano 종료로 무료 경로 실패 시 유료 대체 가용성은 보장하지 않는다.

필수 챌린지의 분석은 `summarize_challenges.py`로 원본을 재현 집계했다. 실패한 가격 개선 기대와 카드 조회가 없는 주문을 성공으로 세지 않은 범위를 REPORT와 각 README에 명시했다.

## 배포 설정 검수

`--buyer-only`는 공식 키트에서 `--deployed` 분기를 건너뛰게 한다. 따라서 기존 실행에서는 자기 가게가 후보에서 제외되지 않았다. `88ba1d7`에서 이 플래그를 제거하고 같은 키·이름으로 구매자 컨테이너만 재생성했다. 13:35:45/48 KST 실제 shop/buyer 등록과 자기 URL 제외를 확인했다. `buyer-deployed-restart.log`, `buyer-deployed-check.log`가 근거다. 종료·주문·일별 요청 한도와 기존 판매자 컨테이너는 유지했다.

제출 `logs/market-kit.log`는 이 수정 뒤 VPS에서 받은 원문이다. 기존 Mac 원문은 `logs/market-kit-mac-20261020.log`로 내용 변경 없이 보존했고 챌린지 재현 스크립트의 원본 경로만 갱신했다. 그 SHA256은 `ce9c26378167abc9f4234ad28f6b2535ee8ce1e5a3fd2d3f3a67bd7c5a72ffb7`로 이전과 같다.
