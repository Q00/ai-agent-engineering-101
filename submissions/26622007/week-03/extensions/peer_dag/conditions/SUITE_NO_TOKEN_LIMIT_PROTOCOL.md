# 출력 토큰 상한 제거 후 45회 재실험 규약

사용자 요청: "아니 출력제한을 없애고 다시 실험해".
이전 batch `20260921T113158-suite-ab4e67`의 max_tokens=2200은 사용자가 요청한 조건이 아니라 구현 에이전트가 임의로 넣은 상한이다.
그 설정에서 45회 중 17회가 정확히 2200 completion tokens, finish_reason=length로 JSON이 잘려 실패했다.
기존 28/45는 그 인위적인 상한을 포함한 설정의 결과이며, 상한 없는 시스템의 성공률로 해석하지 않는다.
원본 로그·결과·규약·실패 커밋은 그대로 보존한다. 이번 결과를 기존 결과에 합산하거나 교체하지 않는다.

## 변경 사항

- 현재 peer_dag/config.json과 기본 config.json의 max_tokens를 제거한다.
- 전송기는 설정에 없는 max_tokens를 요청에 넣지 않으며 대체 숫자를 주입하지 않는다. max_completion_tokens도 보내지 않는다.
- 과거 설정 재현을 위해 명시적으로 설정한 양의 max_tokens를 전달하는 기능은 유지한다. 이번 runner는 토큰 상한이 있는 설정으로 시작하지 못한다.
- CLI의 max_tokens 필수 검사 및 4096 상한을 제거한다.
- 모든 실제 HTTP 요청에서 두 토큰 상한 필드가 없는지, strict JSON Schema response_format이 있는지 별도로 검증한다.
- OpenRouter의 생략한 파라미터는 provider 기본값에 맡겨진다. 제공업체 자체의 출력/문맥 한도까지 제거했다는 뜻은 아니다.
  근거: [OpenRouter Parameters](https://openrouter.ai/docs/api/reference/parameters), 2026-09-22 확인.

## 고정 사항

[기존 규약](SUITE_FINAL_PROTOCOL.md)의 작업, 정답, 프롬프트, 평가 및 재시도 방법을 유지한다.
5개 작업 × baseline/homogeneous/overconfident × 3회 = 45회, 486 scalar facts 검사를 모두 다시 실행한다.
실패한 17개만 골라 재실행하지 않는다. 회차별 작업 및 조건 순서 회전도 동일하다.

모델 deepseek/deepseek-v4.1-flash, Fireworks, temperature=0, reasoning disabled.
requester=A, max_depth=5, max_tasks=12, max_steps=4, max_calls=64, max_parallel=3.
외부 실험은 직렬, 사이 15초, 실행당 600초, 응답 읽기 90초 및 2MB, 소켓 30초.
JSON Schema의 필드 크기/타입 및 업무 검증도 유지한다. 이는 API 출력 토큰 상한과 다른 제약이다.
429는 기존 최대 6회/Retry-After/누적 대기 300초, 기타 전송 오류 최대 2회.
장기 메모리/웹 도구 없음. 동일 작업자의 독립 세션 병렬 실행과 공통 역할표를 유지한다.

실행 전에 코드·규약을 커밋하고 실행 중 hash를 확인한다. 변경이 필요하면 별도 실험으로 기록한다.
매 시도 원본 JSONL/콘솔/result/metrics/하위 산출물을 새 ID로 보존한다. 오류도 분모에 남긴다.
배정/실행 중첩/깊이/사실 벡터/전체 산출물/비용/실제 provider/종료 사유를 재집계한다.
정성 검토는 cases/README.md의 기존 기준을 따른다. 자동 facts 통과와 산출물 완성도를 구분한다.
시간 차이와 모델의 비결정성도 있으므로 이전 결과와의 차이를 모두 토큰 상한 제거의 인과 효과라고 단정하지 않는다.

실행: 저장소 루트에서 `submissions/26622007/.venv/bin/python -u submissions/26622007/week-03/extensions/peer_dag/suite_study.py run`.
기본 Contract-Net 배정 실험 9회는 별도 과거 기록으로 유지하며 이번 45회에 합산하지 않는다.
