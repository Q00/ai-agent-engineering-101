# response_format 필수 적용 및 실호출 검증

2026-09-21. 최종 실호출 소스: `964a2195b7748f244223112ee138726fa5765d8d`.

## 적용 내용

- 기본 입찰은 bid/confidence/reason, peer DAG는 propose/review/execute/synthesize별 JSON Schema를 전송한다.
- 모든 요청에서 `response_format.type=json_schema`, `json_schema.strict=true`를 명시한다.
- 공통 전송기는 형식 누락과 `require_parameters=false`를 요청 전에 거절한다. 미지원 HTTP 응답에서 형식을 제거하는 폴백은 없다.
- 실제 직렬화 바이트와 요청 로그가 같은지 오프라인 테스트로 검사한다. 실호출 로그의 스키마는 현재 단계별 스키마와 대조하고, 원본 응답은 독립 JSON Schema 검증기로 검사했다.
- 기본 입찰과 peer DAG의 제공업체를 Fireworks로 고정했다. 모델은 `deepseek/deepseek-v4.1-flash`를 유지했다.
- 중복 키, 필드 타입, 의존 관계, 깊이, 업무 규칙, 정답 검증은 별도로 유지한다. 기존 JSONL 로그와 결과 CSV는 수정하지 않았다.

## 최종 검증 결과

| 검사 | 결과 |
|---|---|
| 오프라인 테스트 | 기본 20 + peer DAG 29 + 연구 확장 17 = 66개 통과 |
| 기본 입찰 실호출 | 3/3 요청 스키마 일치, 3/3 원본 응답 스키마 통과, 전부 Fireworks |
| peer DAG 실호출 | 20/20 요청 스키마 일치, 20/20 원본 응답 스키마 통과, 전부 Fireworks |
| 단계별 실호출 | propose 12, review 4, execute 3, synthesize 1 |
| 최종 결과 정확성 | 지정 facts 12/12 통과 |
| 실행 추적 감사 | 의존 관계, 실행 상태, 호출 예산 검사 통과 |
| 원본 응답 재생 | 병렬도 1 및 3에서 모든 응답 소비, 두 실행 모두 12/12 통과 |

실호출은 4개 작업, 20회 호출, 약 70.6초, 보고 비용 $0.016946876이었다.
최종 산출물은 두 요금 후보의 수익성 수치와 계산식, 마이그레이션 선행 필요,
필수 테스트 미검증으로 인한 출시 보류를 포함한다. 문자열이 한 글자로 축소됐던 현상은 최종 실행에서 관찰되지 않았다.
실제 최대 동시 모델 호출은 3이었지만 실행 단계는 같은 Worker에 선정되어 최대 동시 1이었다.
따라서 이번 실호출만으로 실제 작업 실행의 병렬 가속을 주장하지 않는다.

## 실패와 중단 기록

1. `20260921-response-format-live-1`: 자동 라우팅, 첫 스키마. 20개 응답은 스키마를 통과했지만
   여러 문장이 한 글자로 축소되고 최종 facts가 누락되어 결과 검사는 0/12였다.
   문자열의 비공백 정규식을 전체 문자열을 명시적으로 허용하도록 수정했다.
   JSON Schema 검증기의 부분 일치와 제공업체의 전체 일치 해석 차이가 원인이라는 판단은 관찰에 근거한 추정이다.
2. `20260921-response-format-live-2`: 대화 중단 후 실행 프로세스와 완료 결과가 없어 부분 로그와 중단 메타데이터를 보존했다.
3. `20260921-response-format-live-3`: 자동 라우팅의 Wafer가 strict 스키마 요청에도 추가 JSON/코드 블록을 반환해 로컬 파서가 거절했다.
   완료되지 않은 실행은 중단해 보존했다. 이후 제공업체를 고정하고 heartbeat 본문 수신에 기한·크기 제한을 추가했다.
4. `20260921-response-format-live-4`: 위 수정 후 전체 흐름 및 결과 검사 통과. 실패·중단 실행을 성공으로 합산하지 않았다.

[OpenRouter 공식 문서](https://openrouter.ai/docs/guides/features/structured-outputs)는 엔드포인트마다
스키마 지원과 강제 수준이 다르다고 설명한다. [Fireworks 공식 문서](https://docs.fireworks.ai/structured-responses/structured-response-formatting)도
정규식 지원의 제한을 명시한다. `strict=true`만으로 제공업체의 모든 구현이나 내용의 정확성을 보장할 수 없다.
이번 결과는 한 번의 완전한 peer 실행 검증이며 반복 실행의 완전한 결정성을 입증한 실험이 아니다.

## 증거와 남은 과제 범위

- [최종 요청·응답·재생 검증](logs/20260921-response-format-final-verification.json)
- [테스트 66개 기록](logs/20260921-response-format-provider-fix-tests.log)
- [실호출 원본 로그](logs/20260921-response-format-live-4.jsonl)
- [최종 산출물](runs/20260921-response-format-live-4/artifacts/release-review.json)
- [기본 입찰 실호출](../../logs/20260921-response-format-base-smoke.console.log)
- [공식 제출 검사](logs/20260921-response-format-final-course-check.log)

기존 `20260921T051850-f6cc75`의 3조건 9회 비교는 response_format 미적용 상태의 원본 결과다.
이번 변경 후 3조건 9회를 다시 실행한 것은 아니며 이전 결과와 합산하지 않는다.
기본 제출 실험은 homogeneous/overconfident 반복과 필수 로그 수가 부족해 공식 검사가 아직 미통과다.
기본 입찰 smoke는 진단이므로 기본 results.csv의 반복 횟수에 포함하지 않았다.
