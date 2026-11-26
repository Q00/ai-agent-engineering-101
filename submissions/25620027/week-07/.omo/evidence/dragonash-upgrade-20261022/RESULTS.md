# dragonash 심층 분석 기반 판매자1.6.0 배포

2026-10-22 Codex. 사용자 요청은1위 사례를 더 면밀히 분석하고 적용 가능한 방안을 구현·배포하는 것이었다. [7단계 비교와 고정SHA 근거](RESEARCH.md).

## 실제 배포와 검증

- **2026-10-22 13:44:21 KST 배포**, 소스69a0dd6. 이미지 `ax-week07-25620027-seller:1.6.0-week-openai-daily5000`, ID `sha256:695bb2e4856b80c8d81cf1af4343900d8c7c7a5571d665b761f46ec56df19894`. [빌드](build.log)·[격리 이미지 확인](image-preflight.log)·[교체](deploy.log).
- 기본상품 요청에 제한된 카탈로그 후보를 미리 제공하고 모델에는decide만 노출한다. 실제 공개 램프·의자 로그에서opening1모델왕복+수락1왕복을 확인했다. 기존 일반 흐름search→decide→다음턴decide의3왕복 중1개를 줄인 것이며, 부하를 통제한 지연33% 단축 실험은 아니다. 상세 상품 요청은search→decide를 유지한다. [실제 모델·turn 시간](after-runtime.log).
- 서비스 질문의'flexible refund policy'를 상품의flexible속성과 구분한다. 공개 기본램프+서비스질문은기본SKU lamp로 열고, 검정·조광·30cm·flexible neck을 요청하면lamp-nocturne를 선택했다. 기존 실제실패사례의조건부보증제안을무조건금액으로받지않는안전경계는유지했다.
- `I accept`·`OK` 등 전체 무조건 수락을 현재ask에 연결한다. 조건부수락·예산·가격비교·삽입지시는결제근거로사용하지않는다. 모델의SKU·실제금액·하한·task종료검사를유지한다.
- 호가본문은쿠폰예고·시장평균·타가게숫자를줄이고그근거를metadata·비교페이지에남긴다. 완료문에는실제제안횟수·판매자서명·정확한금액을알리며특정별점·후기문구를지시하지않는다.

|공개 실제모델 가상거래|체결|SKU|견적 응답|수락 응답|등록 공개키 서명|
|---|---:|---|---:|---:|---|
|기본lamp+서비스질문→I accept|102|lamp|5.14초|5.20초|검증|
|일반chair→OK|183|chair|1.59초|4.94초|검증|
|검정·조광·30cm·flexible neck→I accept|132|lamp-nocturne|5.53초|3.81초|검증|

[원문 wire](public-trades.log)·[가격/SKU/시간/서명 결과](public-trades.json). 세 건은 우리 서버만 호출했고 시장의주문결과·공개후기로제출하지않았다. 이 표는표본3건의응답시간이며p95·다른판매자실측과비교하거나시장순위상승의증거로사용하지않는다.

## 검사와 실패 보존

- 현행코드전체오프라인315/315 [최종 로그](offline-validated.log), 운영가드13개는같은가드코드에서추가통과 [가드 포함325개 로그](offline-release.log). 이후3개는서비스질문경계추가회귀다. 변경파일Ruff통과·strict타입0오류/0경고 [린트](lint-verified.log)·[타입](types-verified.log). 격리이미지는network-none/read-only/개인키없음으로확인했다.
- 구조검사통과 [로그](structural.log). 구조검사가확인한기존공식25/25는이전실행기록이며**1.6.0으로공식25개전체를다시실행한것은아니다**. 기존실패·성공로그를덮어쓰지않았다.
- 첫새테스트는pytest예약인자request때문에수집실패했고이름을고쳤다. 전체검사초기3개는새본문/버전에맞춘실제공개계약기대값을수정했다. 비교값을삭제하는대신metadata의round/mean/count/표본범위를검사한다. 가드8개초기실패는기존증거폴더충돌이었고,기존자료를삭제하지않고VPS_TEST_EVIDENCE를새폴더로지정해13개전부통과했다. 초기로그는모두보존했다.

## 운영 보존과 남은 소개 갱신

13:45 KST healthy/restart0,guard누적7693·불변기준선4354·동일기준선SHA256확인. 기존10월28일17:15 KST종료·무료우선·기존OpenAI대체KST하루5000시도·서명키/거래DB/등록명/URL보존. 자동구매·자동재등록·15분감시는시작하지않았다. Hermes컨테이너ID·시작시각·restart0도같았다. [교체전](before-runtime.json)·[교체후](after-runtime.log).

시장 소개 갱신의 최초 API 시도는 `401 Unauthorized`로 실패했다([원본](listing-stdout.log)). 사용자가 기존 GitHub 시장 로그인 재진행을 승인하고 직접 인증한 뒤, 오류 콜백 화면에서 `/join`으로 돌아가 로그인된 기존 계정을 확인했다. 기존 이름·URL·공개키로 등록 폼을 한 번 제출했으며, **2026-10-22 14:04 KST 공개 GET 검증에서 실서비스와 시장 캐시 소개가 모두 1.6.0**이다. 소유자·가입시각·주소·등록키와 기존 거래 636건을 보존했다. 인증값은 내보내거나 파일에 기록하지 않았고 자동구매는 시작하지 않았다. [검증 원자료](listing-recovery.json) · [검증 로그](listing-recovery-verified.log).

13:46:21 KST최종board:22위·636거래/1217방문·4.50점·후기636·매출115507·profit1987·alive=true. [원자료](final-board.raw). 연구시작636거래/1215방문이어서판매증가효과는아직판단할수없다. 이증분을개선효과라고보고하지않는다.

복구필요시같은private/data/market마운트와현행한도로기존1.5.0이미지를사용한다. 기존baseline·seller-state.sqlite3·paid-quota.sqlite3·로그·서명키를지우거나초기화하지않는다.
