# 1주차: 지출 계산과 결과 저장

## 상태

코드와 설명을 Codex와 함께 준비하고 OpenRouter에서 실제 실행했다. logs/run-01.txt에 전체 실행 기록이 있으며 총지출 57500원, 미정산액 45500원을 계산하고 파일에 저장했다. 제출 전 학생 본인이 코드와 관찰 내용을 검토해야 하며 GitHub PR은 아직 제출하지 않았다.

## 실행

이 폴더를 현재 작업 디렉터리로 사용한다. Python 3.10 이상을 사용한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

OpenRouter 실행은 run_openrouter.py를 사용한다. 이 파일은 제출 폴더 또는 저장소 루트의 .env에서 키를 읽는다. 키 한 줄 형식과 OPENROUTER_API_KEY 또는 OPENAI_API_KEY 설정 형식을 지원한다. 환경변수를 직접 설정해도 된다. 실행 기본 모델은 nvidia/nemotron-3.5-lightning:free이며 OpenRouter 모델 목록에서 무료 요금과 도구 호출 지원을 확인했다. 아래의 기본 모델 설명은 first_agent.py를 직접 실행하는 경우에 해당한다.

API 키는 로컬 환경변수 OPENAI_API_KEY로 설정한다. 키를 문서나 로그에 적지 않는다. 기본 모델은 gpt-4o-mini이며 AGENT_MODEL로 변경할 수 있다. OpenRouter를 사용한다면 OPENAI_BASE_URL을 https://openrouter.ai/api/v1로 설정하고 AGENT_MODEL에 사용할 모델 ID를 지정한다. 무료 모델의 도구 호출 지원은 직접 확인해야 한다.

```bash
mkdir -p logs
python3 -u run_openrouter.py 2>&1 | tee logs/run-03.txt
```

기본 요청은 notes.txt를 읽고 calculator로 총지출과 미정산액을 계산한 뒤 write_note로 한국어 요약을 저장하는 것이다. 예상 숫자는 총지출 57500원, 미정산액 45500원이다. 모델이 작성하는 문구와 호출 순서는 달라질 수 있다. 도구 정의는 first_agent.py의 TOOLS에 있고, 루프 제한은 8회다. write_note는 실행할 때마다 기존 파일에 추가하므로 이전 실행 결과가 남을 수 있다.

실행 시 사용한 Python/패키지 버전, 모델 ID, 기본 URL(키 제외), 실행 명령을 별도 logs/environment.txt에 기록한다. 실제 전체 실행 로그와 생성된 expense_summary.txt를 확인한다. 실패한 실행도 보존하고 다음 시도는 run-02.txt처럼 다른 이름으로 저장한다.

## 직접 확인하고 작성할 내용

- 모델이 read_file → calculator → write_note 순서로 호출했는가? 실제 로그를 근거로 적는다.
- 저장 요청이 없는 계산 질문에서는 write_note를 생략하는가?
- 도구 설명을 바꾸었다면 바꾼 이유와 관찰 결과는 무엇인가?
- TOOLS.md의 초안을 이해한 뒤 자신의 판단과 실제 관찰에 맞게 수정한다.

## 검증과 제출

저장소 루트에서 다음을 실행한다.

```bash
python3 scripts/check_week01.py submissions/26510122/week-01
```

자동 검사는 로그 내용이 실제 에이전트 실행인지 판별하지 않는다. 오프라인 검증만으로 실행 로그 요구사항을 충족하지 않는다.

학생 등록 브랜치: roster-26510122, PR 제목: [roster] 26510122.
과제 브랜치: week-01-26510122, PR 제목: [week-01] 26510122.
학생 등록 PR을 먼저 제출하고 병합한 뒤 과제 PR을 제출한다. 마감은 2주차 수업 시작 전이며 PR 생성 시각 기준이다. 작업 단위별 커밋을 유지하고 squash하지 않는다.
