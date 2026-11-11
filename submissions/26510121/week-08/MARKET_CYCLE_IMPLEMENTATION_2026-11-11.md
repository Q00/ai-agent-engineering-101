# 시장 주기 기반 Shop/Buyer v15 구현·운영 기록

상태: 2026-11-11 14:17 KST 소스 승격, 14:19:58 두 역할 등록 후 운영 중. **경제적 우월성·1위·무중단 가용성이 증명됐다는 뜻은 아니다.** 비용 상한은 사용자의 후속 지시에 따라 해제했다.

## 실제 구현

| 영역 | 구현과 실행 방식 |
|---|---|
| 주기 감독 | `r3/cycle.py`: 별도 daemon이 5초마다 작업을 예약. 준비·연구·공급 lane은 동시 1개 작업으로 제한·통합하며 Main은 기다리지 않음 |
| 시장 관측 | `r3/research.py:analytics`: 60초마다 로컬 공개 정산의 5/20/60/360분 흐름·SKU·관측된 외부 Buyer 선호·경쟁 순위 구성요소 집계. 자기 owner Buyer를 외부 수요에서 제외 |
| 실제 LLM | 통상 20분 주기, 공급 직전·규칙 변경 때 최소 간격을 둔 Luna 분석과 별도 critic 호출. 두 호출 모두 Responses API structured output, 실제 usage 기록 |
| 상하위 전달 | 규칙 hash·시각·근거가 붙은 연구 결과를 짧게 투영해 Main 컨텍스트에 전달하고 사용 기록을 남김. 전체 대화는 붙이지 않음 |
| Shop 사전 계획 | `r3/prewindow.py`: 공급 시작 310초 전부터 early/checked/ready 단계별 수치 계획. 45초 간격 갱신. 실제 후보·현금·비용·Book 추정으로 선택하고 원본 재고/의무를 수정하지 않음 |
| 개장 실행 | 기존 60초 hook 외에 창의 개장 시각을 감시해 공급 실행 요청. hook과 새 실행기는 동일 OS lease를 공유. 실제 호출 전 기존 현금·창·원가·제어 검사를 유지 |
| 계획 무효화 | 규칙·정책·전략 version·창·catalog checksum·만료·실제 자금 검사 실패 시 기존 즉시 계산으로 복귀. 계획을 매입 허가로 간주하지 않음 |
| 공급 중복/불확실성 | 같은 계획/품목의 확정 매입을 중복하지 않으며, POST 결과가 불확실하면 같은 창·품목에 blind retry를 막음. 명시적 거절과 불확실성을 구분 |
| Buyer 사전 준비 | `r3/prepared.py`: 거래 증거 version에 맞춘 경로 통계, 공산품 및 최근 식품 사용 시간 3개 조건의 통계를 주문 밖에서 준비. 20초 TTL과 새 실행 증거 검사로 stale cache 배제 |
| Buyer 현재 조건 | 실제 주문의 acceptable/budget/expiry, 최신 견적·재고·유통기한·서명·인증·결제는 기존 경로에서 확인. 준비 통계가 이를 대체하지 않음 |
| Meta-harness | 매분 표본 부족·실행 guard 폐쇄·framework 평가·연구 상태를 결합. 실제 판정 근거를 연구에 전달하며, 부족한 증거를 우월성으로 승격하지 않음 |
| Skill 조합 | LLM이 고른 신뢰 primitive를 컴파일하고 실행·사용 기록·library 유지. 필수 measure/validate_facts는 컴파일러가 보장. active library는 최대 24개이며 임의 Python/shell 실행은 없음 |
| 전략 후보 | `r3/strategy_lab.py`: 독립 검토된 Buyer probe/gap/diversity 조합과 Shop service-level 후보를 제한된 범위에서 등록. 중복·무효 변화를 걸러 기존 성숙한 실험 평가자로 넘김 |
| 경제적 승격 | 진행 중인 실험을 지우지 않음. 새 Buyer 후보는 기존 실험의 다음 challenger 경로, Shop 후보는 기존 적응 실험 경로로 전달. 모델 찬성은 경제적 승격 권한이 아님 |
| 비용 관측 | `r3/usage.py`: 한국 시간 날짜별 역할·모델 호출 수, 입력/출력·캐시·추론 토큰·토큰 가격 추정. 예약된 불확실 비용은 UTC 날짜별로 별도 표시 |

Main의 일반 거래는 빠른 수치 경로를 유지한다. 모든 거래에 LLM을 동기 호출하는 구조가 아니다. LLM은 주기적 해석·가설·도구 선택·검토를 맡고, 검증된 계획과 성숙 실험을 통해 Main 행동에 연결한다. 자유로운 생성 코드 실행이나 미검증 경제 정책의 즉시 배포는 구현 범위가 아니다.

## 자동 복구

- 주 감독: 15초 tick, 등록 목록 약 30초·board 약 60초 확인. 로컬 카드와 시장 발견 상태를 구분한다.
- 시장에서 300초 이상 정상 검증이 안 되고 정상 peer가 있으며 전체 장애가 아닌 경우, 마지막 전환과 600초 이상 떨어져 있으면 다른 터널을 시도한다. 재등록 수락은 정상 검증으로 세지 않는다.
- `shop_recovery_v15_enabled=true`에서 provider는 강제 고정이 아닌 우선순위다. 같은 키·계정·원장을 유지하며 Cloudflare/localhost/Pinggy 경로를 선택한다.
- 대체 경로는 준비 후 전환하고, registry/board의 연속 정상 확인 3회 뒤 이전 경로를 정리한다. 전체 시장 outage나 미검증 응답에서 무조건 endpoint를 바꾸지 않는다.
- watcher가 **별도 프로세스** `shop_recovery_guard.py`를 자동 기동한다. 이 감시기는 같은 runtime session의 현재 URL을 따라간다. 주 감독 heartbeat가 신선하면 경쟁하지 않고, stale이면 공개 카드·ACP·목록·board·peer를 검증한 뒤 같은 주소의 등록 복구를 수행한다.
- 주 프로세스가 종료되면 기존 watcher가 재기동하며, source 교체는 Buyer의 다음 결제 전 안전 지점에서 drain한 뒤 수행한다. 복구 진단 쓰기 실패가 주 감독을 죽이지 않는 기존 보호를 유지한다.

운영 중 실제 자동 전환·정상 확인·이전 경로 정리 로그를 확인했다. 그러나 16:30에도 공개 카드 200과 시장 offline이 동시에 관측됐다. **자동 복구는 작동하지만 시장 발견의 반복 불안정은 완전히 해소되지 않았다.** 현재 상태는 `.market/shop-health.json`, 독립 관측은 `.market/shop-recovery-guard.json`, 실행 기록은 해당 runtime의 auto-join log에서 확인한다.

## 검증

- 전체 회귀 331개 통과: `logs/stage-v15-full-regression-04.txt`.
- 배포 도구의 live lease·후보 변조·부분 실패 복구·상태 보존 4개 통과: `logs/v15-source-release-fault-tests-01.txt`.
- 실제 기록의 고정된 13개 입력에서 기존 후보 출력과 동일: `logs/v15-parity-summary-20261111T045808770967Z.json`.
- 별도 고정 replay에서 매입 결과 동일·10개 line. 전경 계산 2.2097초 → 0.3526초. 계획 계산 1.3232초는 백그라운드로 이동했다.
- Buyer 경로 통계 결과 동일. 해당 통계 구간 0.1782초 → 0.0212초. 전체 거래의 p95나 시장 네트워크 속도 향상으로 확대하지 않는다.
- 실제 Luna analyst/critic 2회 synthetic smoke 통과: `logs/v15-live-model-smoke-20261111T050844853660Z.json`. 과금은 기존 공유 journal에 기록했으며 후보 발행은 격리된 자료에만 수행했다.
- source manifest/backup: `logs/v15-source-promotion-20261111T051755645862Z.json`. 키·계정·ledger를 교체하지 않았다.

보존한 실패: 첫 full regression의 무제한 budget 가정과 당시 임시 상한 충돌, 실제 모델의 필수 skill 단계 누락에 따른 계약 거절. 전자는 사용자 지시에 따라 무제한 관측 모드로 변경했고, 후자는 필수 scaffolding을 모델이 아닌 컴파일러 책임으로 바꿨다. 실패 로그를 수정하지 않았다.

## 실제 운영 관측 — 16:30 KST

`logs/v15-live-verification-20261111T073001583450Z.json` 기준으로 배포 source mismatch는 없고, 주기 감독과 독립 복구 프로세스가 실행 중이다.

| 항목 | 새 runtime 시작 이후 |
|---|---:|
| 시장 LLM 연구 결과 발행 | 7 |
| LLM 호출 usage 기록 | 16 |
| Skill 실행 | 22 |
| Main 연구 컨텍스트 전달 기록 | 3,030 |
| Buyer 준비 자료 사용 | 203 |
| Shop 준비 계획 사용 | 5 |
| Shop 정산 | 31 |
| Buyer 구매 | 153 |
| cycle supervisor/supply/Buyer worker 내부 오류 | 각 0 |

이는 실제 실행 증거이며, 모델 호출이 수익·순위를 높였다는 인과적 증거는 아니다. 해당 시각에는 Shop이 다시 시장 목록에서 빠져 공개 온라인 검증은 실패했다. 이 결과도 그대로 보존한다. Buyer는 alive였다.

## 비용 상한 해제

현재 설정은 `daily_api_budget_usd=null`, `research_budget_mode=observe`. 주기·동시 작업·입출력 크기·시간 제한은 유지하며 하루 USD 상한으로 호출을 차단하지 않는다.

16:29경 `.market/model-usage-daily.json`의 2026-11-11 KST 관측은 호출 20회(배포 전 검증 포함), 입력 71,422·출력 5,201 tokens, 토큰 가격 추정 약 $0.01147이다. **하루 전체가 아닌 해당 시각까지의 이 Shop/Buyer journal 사용량**이며 다른 앱의 같은 API 키 사용량과 OpenAI 청구서 전체는 포함하지 않는다. 예약 상태의 알 수 없는 비용을 성공 사용량에 합쳐 확정 요금처럼 표시하지 않는다.

조회: `.venv/Scripts/python.exe -c "from r3.state import Journal,policy; from r3.usage import report; import json; print(json.dumps(report(Journal(),policy()),indent=2))"`. 모델을 추가 호출하지 않고 집계만 갱신한다.

## 운용상 경계와 후속 평가

기존 규칙/전략 revision과 성숙 epoch를 유지했다. 새 실험 후보가 생성됐다는 사실, JSON/회귀 통과, 숫자 예측의 향상, 실제 품질 성숙 후 경제적 향상은 서로 구분한다. 최근 기준선과 동일 조건의 실측 비교·성숙 평가를 계속하며, 1위나 모든 미래 시장에서의 우월성을 보장하지 않는다.
