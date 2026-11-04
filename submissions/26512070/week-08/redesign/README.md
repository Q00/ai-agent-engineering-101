# redesign: week-08 하네스를 재설계하는 하네스

라이브 하네스(seller, buyer, outer, 키트; 라운드 3, 11/17 23:59까지)를 **전체** 재설계하기 위한 별도 코드다.

## 규칙 (코드와 테스트가 지킨다)

| 규칙 | 어떻게 |
|---|---|
| 라이브 하네스를 import하지 않는다 | `guard.install()`이 week-08 최상위 모듈(core, agents, outer, seller, ...)의 import를 막는다. `redesign` 패키지를 불러오면 바로 걸린다 |
| 라이브 하네스를 실행하지 않는다 | `subprocess`를 쓰지 않는다 (테스트가 소스를 검사한다) |
| 라이브 하네스에 쓰지 않는다 | 쓰기는 `guard.write()`/`append()`만, `redesign/work/` 아래만 |
| 라이브 하네스 파일은 데이터로만 읽는다 | `guard.read()` (week-08 기준 경로, 읽기 전용) |
| Claude 자원을 쓰지 않는다 | 모델은 `llm.ask()` 하나, OpenRouter만. Claude Code CLI나 Anthropic SDK는 쓰지 않는다 (테스트가 검사한다) |
| 키는 저장하지 않는다 | 라이브 하네스와 같은 사용자 환경변수 `OPENROUTER_API_KEY`를 메모리에서만 쓴다. `work/calls.jsonl`에는 모델, 시간, 토큰, 비용만 남긴다 |

## 사용 (week-08 폴더에서)

```
py -m redesign ping                                   # 모델 연결 확인
py -m unittest discover -s redesign/tests -t .        # 격리 테스트
```

모델은 `config.json`의 `model`에서 바꾼다.
