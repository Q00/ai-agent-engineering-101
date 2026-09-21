## What I built

모임 비용을 읽고 계산한 뒤 파일로 보관하는 에이전트입니다. 기존 read_file/calculator에 write_note를 추가했습니다. 총액은 77,600 KRW, 4명 균등 부담액은 19,400 KRW입니다.

## What I tried and discarded

- 두 도구 기준 실행에서는 계산했지만 저장할 도구가 없었습니다.
- 짧은 설명 A는 금액을 저장했지만 calculator 호출과 저장 파일의 계산식이 빠졌습니다. 설명 B는 calculator를 두 번 호출하고 계산식까지 저장했습니다.
- 명시적 저장 금지 요청에서는 A/B 모두 read_file만 호출하고 정확히 답했으며 파일을 만들지 않았습니다. 이 조건에서는 차이가 관찰되지 않았습니다.
- 인증 환경 변수 누락과 cp949 출력 실패, UTF-8 설정 후 문자 깨짐을 겪었고 직접 UTF-8 캡처로 개선했습니다. 원본 로그 7개를 수정 없이 보존했습니다.
- clock/fetch/split_bill은 설계 검토만 했습니다. 구현 후 실패한 시도로 주장하지 않습니다. 각 조건 1회로, 설명의 일반적 우월성이나 인과 효과를 단정하지 않습니다.
- 사용자와 AI 도우미가 설계를 논의했고 AI 도우미가 구현·문서를 작성했으며 사용자가 실제 모델 실행을 수행했습니다. 작업 단위 커밋과 PROCESS.md에 기록했습니다.

## How to run

정확한 PowerShell 재현 절차는 submissions/26510121/week-01/README.md에 있습니다.

- 실제 실행 환경: Python 3.9.13, openai==2.48.0. 강의 안내인 Python 3.10 이상에서 실제 API 재현은 아직 하지 못했습니다.
- OPENAI_BASE_URL=https://openrouter.ai/api/v1
- AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
- OPENAI_API_KEY는 실행 터미널의 환경 변수로만 제공합니다.
- 제출 폴더에서 `python -m pip install -r requirements.txt` 후 `python capture_run.py description-b`
- 결과 파일을 README 안내대로 보관한 후 `python capture_run.py no-save-ab`
- 기본 설명 B, 최대 반복 8회, temperature/seed 미지정. 모델 응답의 동일성은 보장하지 않습니다.

## Checklist

- [x] `python scripts/check_week01.py submissions/26510121/week-01` passes locally
- [x] Run logs are committed under `logs/`
- [x] No API keys anywhere in the diff (text credential-pattern scan passed)
- [x] History is not squashed
- [x] Offline write/UTF-8/A-B schema checks pass

## Roster dependency

제출 준비 시 roster PR #25는 open/미병합입니다. 학생 요청에 따라 Week 1을 먼저 제출합니다. roster 등록이 병합되기 전에는 소유권 CI가 실패할 수 있으며 이후 브랜치 상태와 CI를 다시 확인해야 합니다. 변경은 모두 submissions/26510121/week-01/ 안에 있습니다.
