# GPT-6 Luna 추론 강도 비교 (low / max, 30턴)

기존 HTML 실습과 같은 영어 프롬프트, 네 화행, 파서, 네 시나리오, 세 조건을 그대로 쓰고
모델과 추론 강도, 턴 상한만 바꾼 확장 실험이다. 과제의 원래 `../results.csv`에는 섞지 않는다.

| 항목 | 값 |
|---|---|
| 모델 | `openai/gpt-6-luna` (OpenRouter, provider `openai` 기본 엔드포인트만, fallback 없음) |
| 추론 강도 | `reasoning.effort` = `low`, `max` (모델의 `supported_efforts`에 둘 다 있음) |
| 역할 | buyer, seller, reader 모두 같은 모델과 같은 강도 |
| 턴 상한 | 30개 메시지. 도달하면 `open` |
| 반복 | 강도 2 × 조건 3 × 시나리오 4 × 반복 3 = 72개 에피소드 |
| temperature, top_p | 모델이 지원하지 않아 설정할 수 없음. 요청에서 생략 |
| seed, max_tokens | 보내지 않음 |
| response_format | 협상 text, reader와 structured는 strict JSON Schema (기존과 동일) |

`null`로 보낸 temperature도 OpenRouter는 요청한 파라미터로 취급해 404로 거절했다
(`runs/luna-probe-20260928`, `../logs/luna-probe-20260928-*`). 과거 실험의 해시 검증 때문에
`../pilot/transport.py`는 바꾸지 않고, 설정에 없는 키를 생략하는 한 줄만 다른 복사본
`transport.py`를 쓴다. 차이는 `test_luna.py`가 검사한다.

```sh
python3 -m unittest discover -s submissions/26622007/week-04/reasoning_effort -p 'test_*.py' -v
python3 -u submissions/26622007/week-04/reasoning_effort/probe.py --env-file submissions/26622007/.env
python3 -u submissions/26622007/week-04/reasoning_effort/run_luna.py --env-file submissions/26622007/.env --jobs 3
python3 submissions/26622007/week-04/reasoning_effort/compare.py
```

`run_luna.py`는 기록된 `(run, scenario)`를 건너뛰고 재개한다. 코드·설정 해시가 다르면
같은 suite를 재개하지 않는다. 작업 순서는 seed 20260928로 섞어 두 강도가 시간대별로 섞이게 했다.
원본 요청·응답은 `../logs/<run>.jsonl`, 결과는 `runs/<suite>/results.csv`,
설정과 프롬프트는 `runs/<suite>/manifest.json`에 있다.
`compare.py`는 모든 행을 로그와 대조하고, 30턴 대화의 첫 8턴을 원래 8턴 runner로 재판정해
DeepSeek 8턴 결과와 같은 상한에서 비교한다.
