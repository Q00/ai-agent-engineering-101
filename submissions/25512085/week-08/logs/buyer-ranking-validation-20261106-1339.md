# 후보 정렬 제한 변경 검증

작업 위치: `submissions/25512085/week-08`. 기존 dirty 파일 및 원본 시장/상태/키/로그는 변경하지 않았다. 실행은 모두 아래 격리 runner의 audit guard 하에서 수행했다. 원본 `.market`, PEM, `state_hub.json`, `seller_history.json`, 기존 logs/history 읽기와 외부 I/O를 차단한다. 이 기록은 새로 만든 검증 로그만 요약한다.

## 실제 명령과 결과 (이 디렉터리에서 실행)

1. `python -B verify_cash_refactor_offline.py baseline`
   - `cash-refactor-baseline-20261106-133542-974889.log`: 기존 288개 통과. buyer 가격/재시도/실패/품절 및 최신 procurement guard 포함. 기준선 커밋 `a075973`.
2. 합성 테스트 추가 후 같은 baseline 명령
   - `cash-refactor-baseline-20261106-133711-289565.log`: 296개 실행, failures=14/errors=3. 절대액 점수 및 잘못된 정가 처리의 예상 실패(하위 subTest 포함). 실패도 보존한 테스트 커밋 `c494ba3`.
3. `python -B -c "import sys; import verify_cash_refactor_offline as v; v.MODULES = ('test_buyer_savings_ranking_offline',); sys.argv = ['verify_cash_refactor_offline.py', 'after']; raise SystemExit(v.main())"`
   - `cash-refactor-after-20261106-133824-454760.log`: 26개 통과. runner가 조건부 모듈을 추가하므로 신규 8개가 두 번 실행되며 cash 모듈 테스트 10개도 포함한다. 로그의 Command 헤더는 runner 내부 argv이며 실제 호출은 이 항목의 `-c` 명령이다.
4. `python -B verify_cash_refactor_offline.py after`
   - `cash-refactor-after-20261106-133836-225013.log`: 전체 296개 통과. 기존 기대값은 변경하지 않았다.
5. `git diff --check`: 오류 없음(CRLF 변환 경고만 있음).

## 변경과 한계

- 기본 효용만 `(list_price-est_pay)/list_price`로 변경하고 500원 하한 제거. 기존 feasibility/trust/bestseller/diversity 배수와 가격·예산·필터·매입 가드는 유지한다.
- goods 누락/0 정가의 기존 예산 fallback, food 누락/0 제외는 유지한다. 잘못된 비숫자/불리언/음수/비유한 가격 및 정수화 후 0은 안전히 제외한다. 원본 정가·예산을 바꾸지 않는다.
- 공식 Saved 집계 전체의 최적화나 실제 성과를 검증한 것은 아니다. goods 누락 정가 fallback은 실제 정가를 확인하지 못하는 기존 한계다.
- `weeks/week-08/README.md`, `scripts/check_week08.py`가 없어 해당 명세/CI 검증은 실행하지 못했다. 로컬 `week-08.html`과 사용자 전달 Patch3.4 설명을 사용했다.
- 네트워크/시장 실행, VM 배포, 설치, push는 하지 않았다. 최종 변경은 로컬 전용이다.
