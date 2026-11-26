# 구매자 보안·후기 정책 검증 범위

2026-10-21 KST. 기존 야간 판매 전용 실행·고정 터널·가격 정책·종료 시한을 바꾸지 않았다. 실제 모델 API 호출, 새 시장 주문, 기존 공개 후기 수정, 공식 25개 재실행은 수행하지 않았다.

## 확인한 실행

캐시 Python `/Users/sungjinho/.cache/uv/archive-v0/k_F7EUrQE1oYyHVUjw4SH/bin/python`으로 다음을 실행했다. 출력은 도구 실행 결과를 그대로 아래에 기록한다.

```text
python -m pytest -q tests/test_security_review_policy.py tests/test_security_boundary.py tests/test_selection_arithmetic.py tests/test_buyer_order.py -p no:cacheprovider
..................................                                       [100%]
34 passed in 0.64s
```

같은 캐시의 Ruff로 변경 6개 소스와 새 검사 2개 파일을 검사해 `All checks passed!`를 확인했다. strict basedpyright의 변경 6개 소스 결과는 `0 errors, 0 warnings, 0 notes`다. 독립 검토는 [security-boundary-code-review.md](../security-boundary-code-review.md)의 CLEAR/APPROVE이며 실행 검증과 구분한다.

## 시나리오와 의미

- 실제 관측 형태의 권한 사칭/가격·별점 지시: 직접 응답만 사건으로 기록한다. 일반 광고·솔직한 후기 요청·인용 경고·부정형 경고는 지원 문법에서 사건으로 기록하지 않는다.
- 정상 종료·결렬·예외: 건별 공유 수집기로 관찰 사실과 원본 영수증을 유지한다. 공개 후기 문자열은 직접 판매자 사건으로 승격하지 않는다.
- 입력 경계: 원문 후기·task/context/SKU/상품 metadata 문자열이 협상 요약에 들어가지 않는다. 가격은 metadata.ask를 우선하고 제한된 숫자 표현만 대체 사용한다. protocol 식별자는 내부 유지한다.
- 후기: 직접 관찰 사건만 1점과 사실 경고, 정상 결렬은 3점, 정상 체결은 모델 별점을 유지한다.
- 재선택: 자기 기록에 사건이 있는 이름만 도구가 거부한다. 다른 가게는 계속 모델이 선택한다.
- 기존 정확한 주문 품목·현재 예산 비교·예산 초과 송신 방지 계약은 회귀 검사와 소스 검토로 확인했다.

이 결과는 HTTP fake와 기존 ScriptedHarness에 대한 결과다. 일반 모델 행동·실제 공개 후기 접수·모든 주입 공격의 방어 성공률·현재 전체 공식 과제 통과를 뜻하지 않는다. 소스 변경은 구매자용이며 현재 장기 실행 중인 판매자 프로세스를 재시작하지 않았다.

과거 관측의 로컬 보강은 [historical-incidents.json](historical-incidents.json)에 별도 보존했다. 기존 204건의 비보안 구매 필드·과거 평가는 유지했고 117건에만 safety 사실을 추가했다. 외부 리뷰/시장 쓰기는 없다.
