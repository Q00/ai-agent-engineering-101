# Week 03 — Contract Net with LLM Contractors

Smith(1980)의 Contract Net을 manager 1명과 LLM contractor A/B/C로 재현한다. 6개 task를 공고하고 contractor가 보고한 `bid`와 `confidence`로 낙찰자를 정한 뒤, 미리 확정한 `gold` contractor와 비교한다. 최종 비교는 로컬 Ollama의 동일 프로토콜로 `baseline`, `homogeneous`, `overconfident`를 각각 3회 실행했다.

## 파일

- `tasks.json`: 실행 전에 확정한 계산 2개(A), 글쓰기 2개(B), Python 2개(C)
- `contract_net.py`: contractor prompt, strict parser, 낙찰 및 지표 계산
- `run_experiment.py`: 조건별 실행, append-only CSV와 로그 저장
- `smoke_test.py`: task 1개 × contractor 3명의 로컬 호출 검사
- `test_contract_net.py`: API를 호출하지 않는 단위 테스트
- `results.csv`: 실패를 포함한 모든 run
- `logs/`, `smoke_logs/`: raw response와 개발·실험 기록
- `REPORT.md`: 설정, 결과, Smith 비교 및 해석

## 최종 프로토콜

| 항목 | 값 |
|---|---|
| provider | `ollama-local` |
| model | `qwen2.5:7b-instruct` |
| base URL | `http://localhost:11434/v1` |
| temperature | `0` |
| max tokens | `1024` |
| 응답 형식 | strict JSON Schema: `bid`, `confidence`, `reason` |
| parser | 응답 전체를 `json.loads`하며 추출·복구하지 않음 |
| fingerprint | `491af965d9c1ac2397cdda28509c4464c02897eb8ecb6ce16955cbbce1c230eb` |

각 run은 batching 없이 `6 tasks × 3 contractors = 18`개의 독립 호출을 수행한다. manager는 `bid=true` 중 confidence가 가장 높은 contractor를 선택하고, 동점이면 A/B/C 응답 순서상 먼저 온 contractor를 선택한다. 메시지는 task announcement 3개, `bid=true`마다 1개, 낙찰 시 award 1개로 센다.

## 재현 방법

```bash
ollama pull qwen2.5:7b-instruct
export OPENAI_BASE_URL=http://localhost:11434/v1
export AGENT_MODEL=qwen2.5:7b-instruct
python submissions/25512082/week-03/test_contract_net.py
python submissions/25512082/week-03/smoke_test.py
python submissions/25512082/week-03/run_experiment.py --condition all --runs 3
python scripts/check_week03.py submissions/25512082/week-03
```

로컬 Ollama에는 실제 API key가 필요 없다. 기존 OpenRouter의 401, JSON parse failure, 429 실행은 삭제하거나 보정하지 않았으며, 최종 비교에는 동일한 Ollama fingerprint로 실행한 run 19~27만 사용한다.

