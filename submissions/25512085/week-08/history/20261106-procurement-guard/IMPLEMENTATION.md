# 5개 goods SKU 추가매입 가드 (로컬, 미배포)

기준 HEAD: `d602568`. `weeks/week-08`은 없음. AGENTS.md 및 등급/보증 정책과 기존 restock·cash 모듈/테스트를 확인했다. seller.py, 판매하한 숫자, 보증, food/forum 판매 로직은 수정하지 않았다. facade의 기존 공개 함수 서명도 그대로다.

## 정책과 적용 순서

- config의 명시적 watchlist: G8095ddd6, G24f5f979, G2698c6f8, Gbec2b68f, Gf6b70da8. goods일 때만 적용하며 다른 SKU/food의 기존 매입 평가·순위를 유지한다. 영구 blacklist를 추가하지 않는다. 기존 no_rebuy/visibility/endgame/예산 가드는 완화하지 않는다.
- 실제 `inv.units`의 live_grade(없으면 grade)와 `seller_cash.sales`의 own=True, 최근 8h settled 행만 사용한다. 공개 판매/문의량은 재고 소진 증거가 아니다. `seller_cash.coverage`가 8h를 덮고 마지막 관측이 120초 이내여야 보유 재고의 추가매입을 검토한다.
- 전체 재고 / (8h 자체 판매 / 8) >=4h이면 보류. 자체 판매 0인데 보유하면 보류. 불완전 관측인데 보유해도 보류한다. B/C 각각 coverage >=4h 또는 무판매 재고 2개 이상이면 보류한다. 무판매 B/C 1개를 포함해 그 외 경우에도 최대 1개만 탐색한다. 이 2개 기준은 혼합 평균이 B/C 축적을 숨기지 않도록 택한 보수적 로컬 기준이다.
- watchlist의 요청량을 먼저 0/1로 제한해 projected surge를 계산한다. 평균/스트레스 평가 후 후보 depth를 보유+허용량 이하로 낮추고, 그 후보로 restore 비용·예산을 계산한다. growth allocator는 복원+우선순위 루프의 누적 funded 수량에도 모델의 0/1을 적용한다. legacy 주문 생성에도 재적용한다. 실제 출력 주문과 예약금이 이 제한을 따른다.
- 기존 supply_window 키와 planned_skus를 그대로 사용한다. 같은 창 반복/JSON 복원 시 중복 주문·중복 예약이 없고, 다음 창에서는 최신 관측으로 재평가한다. 진단의 window_remaining_qty는 현재 호출의 주문 생성 전 잔여량이다. 기존 창 예약 메타데이터가 통째로 유실된 경우까지 복원하는 새 ledger 시스템은 만들지 않았다.

## 경제성 가정 (실측 수익 아님)

- random grade A/B/C 비중 .7/.2/.1, 평균 cost .65/.50/.35와 stress .75/.60/.45 × list × projected surge. stress는 실제 고원가가 확정된 예측이 아닌 보수적 시나리오다.
- price는 기존 reference_price, 없으면 list × .80/.72/.65를 **추정 prior**로 기록한다. q는 기존 refund_probability/보증을 따르며 보증 B는 최소 .15다.
- 각 등급: `price*(1-q) - cost - cost*.01*(8/24) - minimum_contribution`. 최소기여금 helper는 기존 profit_floor 계산을 그대로 추출했으며 판매하한 숫자는 바꾸지 않았다.
- 해당 등급 자체 판매 0 또는 가격 비교 근거 부재이면 양수 기여는 0으로 cap하고 음수는 보존한다. C의 가상 이익으로 A/B 손실을 상쇄하지 못한다. 평균 또는 stress 가중기여 <=0이면 추가매입 0이다.
- 모델 진단에는 이유, grade 재고/자체판매, 전체·grade coverage, 관측 완전성, 가격 근거, q, 비용/보관료/최소기여금, raw/capped 기여, 양 시나리오 합계, surge, 허용량을 기록한다. 판매를 중단하는 규칙은 없다.

## 공유 가격 근거 수정과 영향

goods 무보증 비교는 **numeric explicit 0만** 인정한다. 누락/None/False/True/문자열 '0'/NaN은 거절하며 food는 기존 동작을 유지한다. 이는 watchlist 밖 goods의 공유 reference_price에도 의도적으로 적용되는 근거 정정이다. 근거 없는 C cash first quote가 None이 되어 기존 seller fallback을 사용할 수 있지만, 실제 cost 기반 profit_floor는 동일하다. explicit 0 비교와 하한 동일성, 기존 seller/food/보증/하한 테스트를 함께 통과했다.

## 검증과 보존

- 원본 HEAD 함수를 메모리에서 읽어 재현: unknown warranty를 C 비교로 인정하고 복원 allocator가 watch SKU 40개를 매입하는 두 회귀 테스트가 예상대로 실패했다. `logs/procurement-guard-baseline-reproduction-20261106-124406-116284.log`.
- 첫 신규 테스트 실행: 10개 중 1개 실패. 가격 78,000 fixture는 실제 stress +1,342여서 음수라는 테스트 가정이 틀렸다. 정책을 느슨하게 하지 않고 fixture를 76,000으로 정정했다. 최초 실패 로그 `logs/procurement-guard-narrow-20261106-124135-2327.log`를 보존했다.
- 최종: `.venv/Scripts/python.exe -B -m unittest discover -p test_*_offline.py`와 동일한 unittest discover를 runpy로 실행하여 **456개 통과** (신규 13개). 임시 디렉터리 생성만 고유한 제출 폴더 내부 경로로 강제하고 TEMP/TMP/PYTHONDONTWRITEBYTECODE를 격리했다. stdout/stderr 원문: `logs/procurement-guard-final-20261106-124444-586139.log`.
- 테스트 전후 기존 week-08 파일 전체 SHA-256 비교: `PREEXISTING_FILES_CHANGED: []`. 사용자 dirty 로그/test-report 및 기존 untracked를 덮어쓰지 않았다. 중간 전체 453개 통과 로그도 보존했다. `git diff --check` 통과 (기존 저장소의 LF→CRLF 경고만 있음).
- 제공된 5개 재고/판매 형태를 synthetic fixture로 검증해 모두 추가 0임을 확인했다. 특히 down3 전체 coverage 약1.95h라도 C 7개/자체판매0 때문에 보류한다. 이 검증은 해당 시점 실제 가격·고장률·수익을 다시 수집한 결과가 아니다. 재고 소진과 가격/자체판매 증거 개선 시 1개 재허용 테스트도 통과했다.

외부 접속, VM 접근/배포, 실거래, 설치, commit/push는 하지 않았다. 실시간 데이터·실측 이익 검증 및 VM 배포는 미실행이다. 최종 리뷰/커밋은 메인 세션의 작업이다.

## 메인 검증

- 독립 읽기 전용 리뷰에서 차단 결함 없음. 실제 observer의 coverage 키와 8h 보존, growth/legacy/복원 경로를 확인했다. observer부터 8h 누적까지 연결한 추가 통합 회귀는 미실행이다.
- 메인이 표준 unittest discover 명령을 별도로 실행해 **456개 통과**했다. 원문: `logs/procurement-guard-main-20261106-124957.log`.
- 이번 변경 파일과 신규 검증 기록만 로컬 커밋한다. 기존 사용자 변경은 제외하며 VM 배포·푸시는 하지 않는다.
