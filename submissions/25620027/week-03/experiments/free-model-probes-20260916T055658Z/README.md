# 무료 모델 후보 연결 진단 — 2026-09-16 14:56~14:57 KST

과제 실행과 분리된 가용성 확인이다. 같은 T01 공고·baseline A 프롬프트, temperature=0, max_tokens=1024로 후보를 각각 한 번 호출했다. 모델의 답안을 받기 전에 플랫폼이 요청을 거절했으므로 JSON 준수·모델 성능은 측정하지 못했다. 과제 CSV와 완료 횟수에 포함하지 않는다.

| 모델 | HTTP | 무료 일일 한도 | 남은 횟수 |
|---|---:|---:|---:|
| `google/gemma-4-31b-it:free` | 429 | 50 | 0 |
| `google/gemma-4-26b-a4b-it:free` | 429 | 50 | 0 |
| `nex-agi/nex-n2.5-mini:free` | 429 | 50 | 0 |
| `nvidia/nemotron-3-super-120b-a12b:free` | 429 | 50 | 0 |

공통 오류: `Rate limit exceeded: free-models-per-day`.

공통 헤더 `X-RateLimit-Reset=1789603200000`은 **2026-09-17 00:00 UTC / 09:00 KST**다. 이 정보는 한도 초기화 시각이며 그 이후 모델 응답 성공을 보장하지 않는다. 모델을 바꿔도 같은 계정의 무료 일일 한도를 공유해 이번 요청은 모두 거절됐다.

원본 근거: [probe-results.jsonl](probe-results.jsonl). 인증 헤더·키는 저장하지 않는다. 과제 런타임·설정·모델은 변경하지 않았다. 기존 시간당 재개 작업 `ax-3-7`는 위 시각 이전에는 API를 호출하지 않도록 조건을 추가했다.
