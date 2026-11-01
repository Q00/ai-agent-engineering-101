# 최종 단위 테스트 환경 오류 기록

2026-10-27 최종 검증에서 기본 AX Python으로 `python -m unittest discover`를 실행했으나 `httpx`와 `a2a` 모듈이 없어 테스트 모듈 두 개를 불러오지 못했습니다. 코드 실패가 아니라 실제 운영 환경과 다른 Python을 사용한 것이 원인이었습니다.

첫 교정 시도에서는 Week 07 디렉터리를 작업 경로로 둔 상태에서 `a2a-lab/.venv/bin/python`이라는 상대경로를 사용해 실행 파일을 찾지 못했습니다. 이후 저장소 루트 기준의 절대경로 `/home/user/student/AX/ai-agent-engineering-101/a2a-lab/.venv/bin/python`으로 다시 실행했고, 단위 테스트 5개가 모두 통과했습니다.

이 기록은 최종 성공 결과와 별개로 잘못된 실행 환경에서의 실패 과정을 보존하기 위해 남깁니다.

