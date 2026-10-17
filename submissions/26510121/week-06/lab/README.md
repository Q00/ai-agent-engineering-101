# A2A 실습

강의의 echo → hello → 규칙 기반 판매자 → 단일 하네스 응답 순으로 확인한다.
이 실습은 최종 과제의 모델 기반 협상 구현과 구분한다.

## 파일 구성

- `reference/`: `weeks/week-06/lab/`의 원본 3개 파일을 그대로 복사
- `requirements.txt`: 강의 참조 버전의 Python 패키지
- `run_lab.py`: 로컬 서버와 공식 CLI를 시작하고 강의 체크포인트 확인
- `../.venv/`: 로컬 가상환경 (커밋 제외)
- `../.runtime/`: 공식 CLI 실행파일·다운로드·빈 하네스 작업 폴더 (커밋 제외)
- `../logs/lab/`: 실행별 원본 CLI·서버 로그와 요약

원본: `weeks/week-06/lab/hello_agent.py`, `seller_agent.py`, `lab_cli.sh`.
공식 CLI: https://github.com/a2aproject/a2a-cli (실습 버전 v0.3.0).

설치와 실행 명령 및 실제 결과는 환경 준비 후 추가한다.
