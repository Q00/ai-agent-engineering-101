# 양준석 · 26622007 과제 제출 안내

> **2026-09-16 안내:** 1주차 `convert_units` 구현, 실제 모델 실행과 최신 실행 명령은 [week-01/README.md](week-01/README.md)를 기준으로 합니다. 아래 설정/키 대기/스타터 상태는 2026-09-15 최초 준비 당시의 기록입니다. 현재 의존성은 주차 폴더의 `requirements.txt`를 사용합니다. PR은 로컬 검증 후 제목·본문·변경 경로를 보여드리고, 해당 PR에 대한 사용자 승인을 받은 뒤에만 생성합니다.

## 현재 설정

- GitHub: `Nocha12`
- 개인 Fork (`origin`): https://github.com/Nocha12/ai-agent-engineering-101
- 강의 원본 (`upstream`): https://github.com/Q00/ai-agent-engineering-101
- 최초 등록 PR: https://github.com/Q00/ai-agent-engineering-101/pull/140
- 작업 브랜치: `week-01`
- Python: 3.12.11, 프로젝트 루트의 `.venv/`
- 설치 패키지: `openai`, `anthropic`; 정확한 버전은 `requirements.txt`에 기록
- API 키: 사용자가 새로 제공할 프로젝트 전용 키 설정 대기. API 호출은 수행하지 않음.

## 제출 원칙

1. 본인 파일은 `submissions/26622007/` 안에서 작업합니다. 최초 등록 파일은 `roster/26622007.md`입니다.
2. 주차마다 별도 브랜치를 만들고, **강의 원본의 `main`으로 PR**을 엽니다. Fork에 push한 것만으로는 제출되지 않습니다.
3. PR 제목은 `[week-01] 26622007`처럼 씁니다. 마감 판정은 **PR을 연 시각**입니다.
4. 시도·수정 단위로 커밋하고, 실패한 시도와 원본 실행 로그도 보존합니다. squash나 히스토리 재작성은 하지 않습니다.
5. API 키는 환경변수로만 사용합니다. 키와 `.env`는 커밋하지 않습니다.
6. 매주 `weeks/week-NN/README.md`에서 최신 요구사항을 먼저 확인합니다.

## 1주차 시작

아래 명령은 프로젝트 루트에서 시작합니다.

```bash
source .venv/bin/activate
set -a
source submissions/26622007/.env
set +a
cd submissions/26622007/week-01
```

`first_agent.py`는 공식 `first_agent_openai.py`를 그대로 복사한 시작 코드입니다. 프로젝트 전용 설정 파일 `submissions/26622007/.env`를 먼저 읽어, 기존 실행 환경의 키 대신 이 프로젝트에 설정한 값을 사용합니다. 현재 `.env`의 키 항목은 비어 있으며, 새 키가 설정될 때까지 API 실행은 보류합니다. `.env`는 Git에서 제외되며 로컬 파일 권한은 소유자 읽기·쓰기만 허용합니다.

시작 코드의 기본 모델은 `gpt-4o-mini`입니다. `AGENT_MODEL`로 모델을 바꿀 수 있고, OpenRouter를 사용할 때는 `OPENAI_BASE_URL`과 해당 제공자의 키·모델을 함께 맞춰야 합니다. 키 값은 문서와 로그에 적지 마세요.

현재 도구는 `calculator`, `read_file` 두 개입니다. 과제는 **세 번째 도구를 추가하고 실제 태스크를 해결하는 것**입니다.

- `first_agent.py`: 세 번째 도구와 스키마를 추가
- `TOOLS.md`: 새 도구 설명을 왜 그렇게 작성했는지 한 문단
- `notes.txt`: 태스크에 사용할 입력
- `logs/`: 실제 에이전트 실행의 콘솔 출력

도구를 추가하기 전에도 시작 코드를 실행해 관찰할 수 있습니다. 아래 실행은 설정된 제공자의 API를 호출합니다.

```bash
: "${OPENAI_API_KEY:?프로젝트 전용 API 키를 .env에 설정한 뒤 다시 불러오세요}"
mkdir -p logs
set -o pipefail
python first_agent.py 'Read notes.txt and sum the numbers in it.' 2>&1 | tee "logs/run-$(date +%Y%m%d-%H%M%S).txt"
```

로그 파일은 실행마다 새 이름을 쓰고 덮어쓰지 마세요. 모델, 실행 명령, 관찰한 행동과 실패한 시도는 `PROCESS.md`에도 기록하세요.

## 검사와 제출

최초 roster PR이 원본에 병합되면, `week-01`에서 원본의 최신 상태를 가져옵니다. 등록 PR이 아직 열려 있으면 과제 작업을 계속하고, 과제 PR은 등록 병합 후 여는 것을 권장합니다.

```bash
cd "$(git rev-parse --show-toplevel)"
git switch week-01
git fetch upstream
git merge upstream/main
source .venv/bin/activate
python scripts/check_week01.py submissions/26622007/week-01
```

**초기 설정 상태에서는 과제 검사가 통과하지 않습니다.** 세 번째 도구, 완성된 `TOOLS.md`, 실제 실행 로그를 작성한 다음 다시 검사하세요. 검사 통과는 구조 요건 충족이며 최종 성적 판정은 아닙니다.

작업 단위마다 변경 파일을 검토하고 커밋합니다.

```bash
git diff
git add submissions/26622007/
git diff --cached --check
git diff --cached
git commit -m 'feat(week-01): describe the completed unit of work'
```

과제가 완성되고 검사가 통과하면 제출합니다.

```bash
git push -u origin week-01
gh pr create --repo Q00/ai-agent-engineering-101 \
  --base main --head Nocha12:week-01 \
  --title '[week-01] 26622007' \
  --template .github/PULL_REQUEST_TEMPLATE.md
```

PR 본문에는 만든 것, 시도하고 버린 것, 실행 방법과 모델을 적습니다. 생성 후 `gh pr checks --repo Q00/ai-agent-engineering-101 <PR번호>`로 자동 검사 결과를 확인하세요.

## 다음 주차

완료된 주차의 브랜치를 재사용하지 않고, 최신 `main`에서 새 브랜치를 만듭니다. 커밋하지 않은 작업이 있다면 먼저 해당 주차 브랜치에 보존합니다.

```bash
git switch main
git fetch upstream
git merge --ff-only upstream/main
git push origin main
git switch -c week-02
mkdir -p submissions/26622007/week-02
cp -R weeks/week-02/starter/. submissions/26622007/week-02/
```

2주차부터는 해당 명세와 검사 스크립트를 사용합니다. 3주차에는 시작 코드가 없고, 명세의 데이터 형식에 맞춰 직접 구성합니다.

## 환경 재설치

다른 컴퓨터에서 프로젝트 루트 기준으로 실행합니다.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r submissions/26622007/requirements.txt
source .venv/bin/activate
```

새로 복제한 컴퓨터에서는 `cp submissions/26622007/.env.example submissions/26622007/.env`로 로컬 설정 파일을 만들고, `chmod 600 submissions/26622007/.env`를 실행합니다. 새 프로젝트 키와 해당 제공자 설정을 입력한 뒤 시작 절차의 `source` 명령으로 불러옵니다.

## 공식 안내

- [강의 홈](https://wpti.dev/ai-agent-engineering-101/index.html)
- [1주차 강의: 저장소 포크와 첫 PR](https://wpti.dev/ai-agent-engineering-101/week-01.html)
- [저장소 제출 규칙](https://github.com/Q00/ai-agent-engineering-101#how-submission-works-fork-and-pr)
- [최초 roster 등록](https://github.com/Q00/ai-agent-engineering-101/tree/main/roster)
- [1주차 과제 명세](https://github.com/Q00/ai-agent-engineering-101/tree/main/weeks/week-01)
