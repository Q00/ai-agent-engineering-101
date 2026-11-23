# 2026-09-22 대체 제공업체 진단

Fireworks 공유 풀에서 429가 반복되어 같은 모델과 현재의 전체 JSON Schema를 처리할 수 있는 대체 경로를 확인했다.
본 45회 실험의 provider는 변경하지 않았고 아래 호출은 실험 성공률·비용에 합산하지 않는다.
첫 번째 출시 검토의 실제 propose/review/execute/synthesize 메시지와 스키마를 그대로 사용하고 provider.only만 바꿨다.
max_tokens/max_completion_tokens는 모두 생략했다. 모든 실제 요청에서 strict response_format이 유지됐다.
공개 endpoint metadata의 structured_outputs 지원 표기만으로 현재 스키마 전체의 호환성을 가정하지 않았다.

|제공업체|HTTP 시도|성공 단계|나머지 결과|원본|
|---|---:|---|---|---|
|DeepInfra|4|review|propose: uniqueItems 미지원, execute/synthesize: propertyNames 미지원. HTTP 200 오류 envelope로 반환됨.|[결과](20260922T015241-no-token-limit-deepinfra-2af21f/result.json), [요청·응답](20260922T015241-no-token-limit-deepinfra-2af21f/events.jsonl)|
|Morph|7|review, execute|propose와 synthesize는 각각 2회 응답 수신 기한 초과, execute는 첫 시도 시간 초과 후 정상 응답. 지연 원인은 미확정.|[결과](20260922T015406-no-token-limit-morph-867191/result.json), [요청·응답](20260922T015406-no-token-limit-morph-867191/events.jsonl), [콘솔](../logs/20260922-no-token-limit-morph-probe.log)|
|Together|4|review|propose: uniqueItems 미지원, execute/synthesize: propertyNames 미지원. HTTP 400.|[결과](20260922T015756-no-token-limit-together-a9b7f2/result.json), [요청·응답](20260922T015756-no-token-limit-together-a9b7f2/events.jsonl), [콘솔](../logs/20260922-no-token-limit-together-probe.log)|

스키마 필드를 제거하거나 json_object/프롬프트 지시로 낮추지 않았다. 네 단계 전체를 통과한 대체 제공업체는 이 진단에서 없었다.
Fireworks 응답이 회복된 후 원래 설정으로 본 실험을 계속했다. 이 결과로 제공업체의 일반적인 우열이나 영구적인 미지원 상태를 주장하지 않는다.
