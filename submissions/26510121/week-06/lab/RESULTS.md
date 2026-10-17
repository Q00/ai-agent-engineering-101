# A2A 실습 실행 기록

## 시도 1: echo 카드 검사에서 중단

- 로그: `logs/lab/20261017T034425.053443Z/`
- CLI: 공식 a2a 0.3.0. Python 3.12.14, a2a-sdk 1.2.2, uvicorn 0.54.0.
- 실제 카드: `name=Echo Agent; interface={'url': 'http://127.0.0.1:8080', 'protocolBinding': 'HTTP+JSON', 'protocolVersion': '1.0'}`
- 실패: `AssertionError: echo_card: ... 'protocolBinding': 'HTTP+JSON' ...`
- 원인: 실습 실행 도구가 모든 서버의 binding을 JSONRPC라고 가정했다. 공식 CLI echo 서버의 기본값은 HTTP+JSON(REST)이고, 과제의 판매자는 JSONRPC를 요구한다. 서로 다른 검사 조건이 필요하다.
- 다음 수정: echo는 HTTP+JSON, Python hello·판매자는 JSONRPC인지 각각 검사한다. 서버 예제 자체는 변경하지 않는다.

이 기록은 실습용이며 최종 과제의 `REPORT.md` 또는 공식 `test-report.json`을 대신하지 않는다.
