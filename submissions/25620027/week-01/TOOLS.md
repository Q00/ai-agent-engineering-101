# Tool-description rationale

새 도구 `text_stats`의 설명을 “Count words, lines, and characters in supplied text. Pass file contents, not a path; call read_file first.”로 작성했다. 첫 문장은 이 도구가 반환하는 결과를 구체적으로 한정해 계산기와의 역할 중복을 피하고, 두 번째 문장은 모델이 파일 경로를 잘못 전달하지 않도록 입력의 의미를 명시한다. 특히 `read_file`을 먼저 호출하라는 문구는 파일을 읽은 결과를 `text_stats`에 넘기는 순서를 알려 주되, 최종 답을 직접 지시하지 않으므로 모델이 세 도구 중 필요한 도구를 선택하는 행동을 관찰할 수 있게 한다.

## Reproduce

- 기준 API: OpenAI API
- 요청 모델 ID: `gpt-5-mini`
- 성공 로그에서 확인된 실제 모델 ID: `gpt-5-mini-2025-08-07`
- Python 의존성: `openai`
- 실행 위치: 이 디렉터리
- 도구 스키마: `calculator(expression: string)`, `read_file(path: string)`, `text_stats(text: string)`이며 세 인자는 모두 필수다. 전체 JSON 스키마는 `first_agent.py`의 `TOOLS`에 있다.

```bash
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_API_KEY=<your OpenAI API key>
export AGENT_MODEL=gpt-5-mini
uv run --isolated --with openai python first_agent.py 2>&1 | tee logs/run-openai-01.txt
```

API 키는 파일이나 저장소에 넣지 않고 실행할 터미널의 환경변수로만 전달한다. 프로그램은 첫 응답의 실제 모델 ID를 `[model]` 줄로 출력하므로 모델 별칭이 어떤 버전으로 처리됐는지 로그에서 확인할 수 있다.

## Observation

두 성공 실행 모두 모델이 `read_file` → `text_stats` → `calculator` 순서로 세 도구를 선택했고 비용 계산 결과는 `45500`으로 같았다. 다만 OpenRouter가 선택한 `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`는 읽은 내용을 `text_stats`에 다시 전달할 때 마지막 줄바꿈을 제외해 237자로 계산했고(`logs/run-03.txt`), OpenAI의 `gpt-5-mini-2025-08-07`은 마지막 줄바꿈까지 전달해 원본과 같은 238자로 계산했다(`logs/run-openai-01.txt`). 이는 세 번째 도구를 고르는 순서는 같아도, 모델에 따라 앞 도구의 결과를 다음 도구의 인자로 보존하는 정확도가 달라질 수 있음을 보여준다.
