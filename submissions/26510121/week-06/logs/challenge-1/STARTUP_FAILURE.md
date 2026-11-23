# 첫 검사 도구 시작 실패

2026-10-17, `python -u lab/check_catalog.py`, exit code 1.
실행 폴더는 `run-20261017T055926.437372Z`이다.
서버 로깅 설정 도중 중단되어 모델 호출·A2A 거래·summary.json 생성은 없었다.
stdout console.log는 생성됐지만 traceback은 도구의 stderr로만 나갔으므로 원본 console에 덧붙이지 않는다.

실제 도구 stderr의 핵심 행:

```text
File ".../uvicorn/logging.py", line 42, in __init__
    self.use_colors = sys.stdout.isatty()
AttributeError: 'Tee' object has no attribute 'isatty'
ValueError: Unable to configure formatter 'default'
```

원인은 검사 도구의 Tee가 write/flush만 구현한 것이다. 다음 수정에서 isatty를 구현하고,
새 실행 폴더로 재검증한다. 이 기록은 원본 로그를 수정하지 않고 추가한 설명이다.
