# AX homework evaluator 상세 사용 가이드

[README로 돌아가기](../README.md)

저장소 루트에서 `cd homework-evaluator`로 이동한 뒤 아래 명령을 실행합니다.

AX 과제 PR의 코드·로그·결과·보고서·커밋 이력을 읽어 **사람 검토용 평가 초안**을
만듭니다. 학생 코드를 실행하거나 import하지 않습니다. 학생의 LLM 실험, 서버,
테스트, README의 명령도 실행하지 않습니다. 채점 과정에서는 GitHub에 게시하지
않으며, 별도로 승인받아 `publish --write`를 실행한 경우에만 코멘트를 게시합니다.

## 결과물과 평가 방식

- `snapshot.json`: 수집한 원문, 파일 해시, PR·head·명세 SHA, 이력
- `bundle.json`, `coverage.json`: 자동 검사와 모델에 보여준 근거·생략 범위
- `prompt.txt`, `response.schema.json`: 모델 독립적인 평가 입력과 출력 계약
- `evaluation.json`: 항목별 판정, 점수, 보류, 근거, 판정 주체
- `feedback.md`: 잘한 점·수정 요청·개선 제안·확인 필요 사항을 포함한 한국어 초안
- `pr-comment.md`: 주요 장점·수정 요청·재실험 필요 여부를 담은 학생용 코멘트
- `publication.json`: 코멘트 해시·평가 대상 PR/커밋·게시 가능 상태
- `request.json`, `raw-response.txt`, `provider-metadata.json`: 실제 모델 호출 시 감사 기록

평가 축은 **필수 요구사항**, **리포트와 구현·결과의 일치**, **과제 의도**,
**추가 시도의 가치·타당성**입니다. 수정 요청은 어느 영역에서든 발생할 수 있습니다.
OpenRouter, Claude Code, Codex 중 무엇을 썼는지는 가감점 사유가 아닙니다.

기본점수는 **명세의 필수 요구사항만 항목당 1점**입니다. 과제별 평가표에
필수항목을 정하고 각 항목의 `source_requirement`에 명세 근거를 기록합니다.
항목 묶음은 비공식 채점 설계이며 교수자의 공식 배점이 아닙니다.
같은 과제에는 동일한 명세·루브릭 버전을 적용하고, 학생별 추가 내용에 따라
필수항목이나 분모를 늘리지 않습니다. 통과 1점, 미충족 0점이며
증거가 부족하면 `pending`으로 남깁니다. 보류를 0점으로 확정하거나 분모에서
제거하지 않고 현재 확보 점수와 가능한 범위를 함께 표시합니다.

`category=requirements`만 점수에 포함됩니다. `consistency`·`intent`는 무배점
검토 영역으로, 한 영역 안에서 필요한 만큼 주장과 오류를 대조합니다. 이 영역의
`fail`·보류도 기본점수·분모에 영향을 주지 않지만 수정 요청과 검토 완료 여부에는
반영됩니다. 결과 JSON은 `required_ids`·`qualitative_ids`·`qualitative_pending`을
분리하고 보고서도 필수 점수표와 정성 평가표를 따로 표시합니다.
기존 항목 ID의 F/C/I 접두사는 식별자일 뿐 배점 여부를 뜻하지 않습니다.

점수와 피드백은 분리합니다. 모델은 `pass_rule`·`fail_rule`에 따라 판정하며,
`severity`로 문제 중요도를 기록합니다. 경미한 표현 오류는 감점할 수 없고,
통과 항목에도 수정 요청을 남길 수 있습니다. 필수항목 미충족에는 핵심 요구사항에 미치는
영향을 설명한 `deduction_reason`이 필요합니다. 같은 결함을 중복 감점하지 않도록 검토합니다. 중복 감점 여부의 의미 판단은
모델·사람의 역할이며, 코멘트 통합이 점수를 자동으로 합치는 것은 아닙니다.

정성 검토가 필수요건의 미충족을 입증하면, 모델은 해당 필수항목에 근거와 영향을
기록해 별도로 판정합니다. 정성 영역 자체의 `deduction_reason`은 비워야 합니다.
추가 계산의 오류만으로 필수 실험 전체를 감점하지 않습니다. 반대로 그 오류가
필수 분석을 무효화하면 관련 필수항목에서 판정합니다. 자동검사 결과를 임의로
덮어쓰지 않으며 검사기 결함은 검사기 수정·버전 변경 후 다시 평가합니다.

0.4의 `evidence-review-v2` 계약에서는 다음 검토 기록도 필수입니다.

- `success_audit`: 실제 최종 답과 근거, 성공 판정 코드, 검증 응답 처리를 각각
  근거 행과 함께 검토합니다. 학생 로그의 O 표시와 결과표가 같다는 것만으로
  정답을 확인했다고 보지 않습니다. 해당하지 않는 영역도 이유와 근거가 필요하며,
  미검토/증거 부족 영역이 있으면 코멘트를 완료 처리하지 않습니다. 무배점입니다.
- `rerun_basis`, `rerun_reason`: 수정 후 실행 동작을 검증해야 하면 `needed`,
  기존 기록으로 계산·문서를 바로잡을 수 있으면 `not-needed`로 판단합니다.
  대응 로그가 부족하면 `unknown` 또는 `needed`로 두고 추가 실행이 필요한 조건을
  설명합니다. 코드가 판단 사유와 상태의 모순을 거부합니다.
- `revision_key`: 같은 원인의 수정 요청은 공통 키와 하나의 통합 `feedback`을
  사용합니다. 항목별 근거·점수는 보존하며 학생 코멘트는 키별 한 번만 출력합니다.
  같은 키에 다른 수정 문구를 넣으면 누락 방지를 위해 검증을 실패시킵니다.

의미 판단과 근거 해석은 여전히 모델/사람의 역할입니다. 검토가 옳다는 것까지
코드가 보증하지는 않습니다. 기존 평가 결과는 보존하며, 현재 버전으로 평가하려면 같은
스냅샷·배점 기준으로 새 `prepare` 디렉토리를 만들고 새 검토를 입력해야 합니다.

가산 정책은 시작 인터뷰에서 받습니다. 명시 기준이 있으면 이름·충족 조건을
저장하고, 충족한 기준마다 1점을 제안합니다. 없더라도 필수사항 밖의 의미 있는
추가 기여마다 1점을 제안합니다. 양쪽 모두 1점으로 고정하며 가중 배점 정책은 거부합니다.
명시 기준이 있어도 다른 추가 기여는 평가합니다. 상한은 인터뷰에서 선택하며,
빈 입력은 상한 없음입니다. 기존의 임의 상한 3점은 기본값에서 제거했습니다.
같은 수업의 제출물에는 동일한 저장 정책을 재사용하세요.

가산점은 필수 결함을 상쇄하지 않습니다. 같은 기여에는 같은 `contribution_key`를
사용해 명시 기준과 자유 추가 기여의 중복 가산을 차단합니다. 이름을 바꾼 동일
기여를 인식하는 의미 판단은 모델·사람의 역할입니다. 배점과 상한 계산은 코드가 합니다.

이 세부 배점은 공식 채점표가 아닙니다. 과제별 평가표의 항목 정의·상한을
검토한 뒤 버전을 정해 사용하세요. 새 과제는 아래의 review-only 설정으로 시작할 수 있으며,
주차 전용 자동검사는 별도 검사기 작업이 필요합니다.

## 시작 인터뷰

`prepare`에 저장 정책을 지정하지 않으면 가산점 인터뷰를 시작합니다. 여러 PR을
같은 기준으로 평가할 때는 먼저 한 번 인터뷰하고 결과를 재사용하세요.

```bash
uv run python -m ax_evaluator interview --out runs/course-bonus-policy.json
```

질문 순서는 명시 가산점 기준 유무 → 있으면 항목 이름·충족 조건/근거 반복
입력 → 전체 가산점 상한입니다. 의미 있는 추가 시도는 기본적으로 계속 평가합니다.
학생 보고서에 적힌 희망 배점은 교수자 정책으로 취급하지 않습니다.
자동화 환경에서는 `--policy`가 필수이며, 입력이 끊긴 인터뷰는 임의 답변으로
채우지 않고 실패합니다. `bundle.json` 안에 정책과 해시가 보존됩니다.

## 빠른 시작: 모델 비용 없는 검사

Python 3.11 이상, `uv`가 필요합니다. GitHub 수집에는 `git`과 로그인된 `gh`도
필요합니다. Python 런타임 외 프로젝트 의존성은 없습니다.

아래의 `PR_NUMBER`, `PR_NUMBER_1`, `PR_NUMBER_2`, `week-NN`,
`FULL_SPEC_COMMIT_SHA`는 실제 대상 값으로 바꿉니다.
`runs/assignment-rubric.json`은 해당 과제의 명세에 맞게 작성한 평가표입니다.

```bash
uv sync --locked

# 원본 repository와 spec-ref를 평가 묶음 전체에서 동일하게 사용합니다.
uv run python -m ax_evaluator collect-pr PR_NUMBER --week week-NN \
  --repo Q00/ai-agent-engineering-101 \
  --spec-ref FULL_SPEC_COMMIT_SHA \
  --out runs/submission-snapshot.json

uv run python -m ax_evaluator prepare runs/submission-snapshot.json \
  --rubric runs/assignment-rubric.json --policy runs/course-bonus-policy.json --out runs/submission-offline
```

`collect-pr`는 PR 제목에 의존하지 않고 변경 경로에서 학번을 찾습니다. PR head와
명세 commit을 고정하고, 새로운 임시 bare Git 저장소에서 blob만 읽습니다. 학생
브랜치를 작업 디렉터리로 checkout하지 않습니다. roster와 PR 작성자, 변경 경로는
수집하지만 소유권·지각·중복 PR을 자동 감점하지 않습니다. 마감·재제출 정책은
아직 결정되지 않았습니다.

위 명령의 입력값은 실제 평가 대상으로 바꿉니다. 새 평가는 새로운 출력 경로를 사용하며 기존
평가를 덮어쓰지 않습니다. `runs/`는 Git에서 제외되어 학생 원문·리뷰가 실수로
PR에 포함되지 않습니다. 크래시나 비정상 모델 응답도 덮어쓰지 말고 보존합니다.

로컬 제출 폴더도 가능합니다. 이 방식에는 Git 이력이 포함되지 않으므로 이력
판정은 보류해야 합니다. 출력은 학생 폴더 밖에 둡니다.

```bash
uv run python -m ax_evaluator collect-local /path/to/student/week-NN \
  --week week-NN --spec ../weeks/week-NN/README.md --out runs/local-snapshot.json
uv run python -m ax_evaluator prepare runs/local-snapshot.json \
  --rubric runs/assignment-rubric.json --policy runs/course-bonus-policy.json --out runs/local-offline
```

## 모델을 연결한 평가

`prepare`까지는 모델 호출이 없습니다. `judge` 또는 `batch --stage evaluate`를 실행할 때
설정한 모델에 근거 패킷을 보냅니다. CLI 호출도 구독 사용량이나 API 비용을
발생시킬 수 있으며, 로컬 endpoint 외에는 제출 자료가 외부로 전송됩니다.

`configs/` 예시를 `runs/` 아래 작업용 설정 파일로 복사해 `YOUR_...` 모델명을
실제로 사용할 모델로 바꾸세요. 키 값은 설정에 넣지 않고 환경변수 이름만 둡니다.
모델은 자동 선택·대체하지 않습니다. 평가 모델은 학생이 사용한 모델과 별개입니다.

| 설정 예시 | 연결 방식 |
|---|---|
| `configs/codex.json` | `codex exec`; 기존 CLI 인증 사용 |
| `configs/claude.json` | `claude -p`; 기존 CLI 인증 사용 |
| `configs/openai-compatible.json` | `/v1/chat/completions`; base_url과 키 환경변수 지정 |
| `configs/anthropic.json` | Anthropic `/v1/messages` |
| `configs/local.json` | 이미 실행 중인 Ollama 등 OpenAI 호환 로컬 endpoint |

```bash
# runs/provider.json에 선택한 설정과 실제 모델명을 준비한 뒤 실행
uv run python -m ax_evaluator judge runs/submission-offline \
  --config runs/provider.json --out runs/submission-model-01

uv run python -m ax_evaluator finalize runs/submission-offline \
  runs/submission-model-01/response.json --out runs/submission-reviewed-01
```

모든 provider가 같은 rubric·패킷·JSON 계약을 사용합니다. 응답 검증이나 연결이
실패하면 점수를 생성하지 않고 기록을 남깁니다. 자동 재시도와 모델 대체는 없습니다.
유효한 response를 재사용해 `finalize`하면 추가 모델 비용이 들지 않습니다.

CLI는 빈 임시 작업 폴더에서 실행합니다. Codex는 사용자 config, shell, Code Mode,
멀티에이전트, 웹 검색·skill 탐색을 끄고 read-only로 실행합니다. Claude는 도구,
MCP, hooks·settings·skill 자동 로드를 제한합니다. **CLI flag 호환성은 버전에
따라 달라집니다.** 개발 환경의 `codex-cli 0.162.1`, Claude Code `2.1.296` help에
맞춘 어댑터이며, 테스트는 가짜 응답을 사용합니다. 실제 유료·구독 호출의 종단 간
동작과 모델별 판단 품질은 아직 검증하지 않았습니다. 구버전에서 실패할 경우
어댑터를 갱신하고 우회 권한을 켜서 실행하지 마세요.

OpenAI 호환 endpoint는 chat completions 메시지·응답 형식을 지원해야 합니다.
`structured_output=true`는 JSON Schema를 지원하는 endpoint에서만 설정합니다.
기본값은 텍스트 JSON 요청 후 로컬 검증입니다. `parameters`로 temperature,
seed, 토큰 제한 등을 명시할 수 있으나 지원 여부는 모델마다 다릅니다. 동일한
seed·temperature도 새 모델 판단의 동일성을 보장하지 않습니다. CLI에는 API
parameters를 전달하지 않습니다. 로컬 서버를 설치하거나 시작하는 기능은 없습니다.

## 사람 또는 다른 에이전트가 검토하기

`review-template.json`을 복사해 항목별 판단과 근거를 채운 다음 가져올 수 있습니다.
현재 대화의 에이전트를 쓰는 경우에도 자동 검사의 근거와 전체 원문을 확인하세요.
프로젝트의 `skills/assignment-review/SKILL.md`는 특정 모델에 종속되지 않은 지침이며
하네스가 프롬프트에 포함합니다. 전역 skill 설치는 필요하지 않습니다.

```bash
uv run python -m ax_evaluator import-review runs/submission-offline \
  runs/my-review.json --reviewer reviewer-name --out runs/human-response.json
uv run python -m ax_evaluator finalize runs/submission-offline \
  runs/human-response.json --out runs/submission-human-01

# 전체 원문의 정확한 행을 읽기. 이 명령은 코드를 실행하지 않습니다.
uv run python -m ax_evaluator evidence runs/submission-snapshot.json REPORT.md --start 1 --end 20
```

자동 검사 항목을 모델/사람 응답으로 덮어쓰는 것은 허용하지 않습니다. 자동 검사에
오류가 있다면 검사기 또는 정책을 수정·버전 관리하고 다시 `prepare`하세요. 수동
판단을 바꿀 때는 새 response와 출력 폴더를 만들어 이전 판단도 남깁니다.

현재 세션 모델의 판정은 `import-review --kind current-session`으로 가져옵니다.
사람이 직접 쓴 판정인 것처럼 기록하지 않습니다. 0.2 버전에서는 중요도·감점 사유·
보류 사유·가산 기준 ID 등이 추가됐으므로 이전 응답을 그대로 재사용하지 마세요.
새 패킷과 템플릿으로 재평가하고 이전 결과는 보존합니다.

## 학생용 코멘트와 게시

`finalize`는 `pr-comment.md`도 생성합니다. 점수표 대신 주요 장점, 핵심 수정 요청,
근거 링크, 모델 실험 재실행 필요 여부를 담습니다. 중요 수정 요청은 누락하지 않고,
경미한 지적은 상세 보고서에 남깁니다. 통과 항목에도 중요한 수정 요청은 표시합니다.
교수자 정책 보류는 학생의 잘못으로 표현하지 않습니다.

```bash
# 오프라인 미리보기. GitHub를 호출하지 않습니다.
uv run python -m ax_evaluator publish runs/submission-reviewed-01

# 코멘트 내용을 검토하고 게시를 승인받은 뒤에만 실행
uv run python -m ax_evaluator publish runs/submission-reviewed-01 --write
```

`--write`는 GitHub 일반 PR 코멘트를 생성하며, 현재 인증 계정이 쓴 같은 PR·주차의
하네스 표시 코멘트가 있으면 갱신합니다. 내용이 같으면 다시 쓰지 않습니다.
교수자나 학생의 다른 댓글은 수정하지 않습니다. GitHub의 Approve/Request changes
리뷰 상태를 변경하는 기능은 아닙니다. 토큰 권한은 로그인된 `gh` 계정의 권한입니다.

미검토·증거 부족·미완료 가산 평가, 바뀐 코멘트 파일, 변경된 제출 커밋, 닫힌 PR은
게시를 막습니다. 교수자 정책 보류만 남은 경우에는 잠정 점수를 노출하지 않는 피드백
코멘트를 게시할 수 있습니다. 게시 직전에 head를 다시 확인하지만 GitHub 댓글 쓰기는
head 조건부 원자 연산이 아니므로 직후 커밋 변경의 경합은 완전히 막을 수 없습니다.
같은 PR의 게시 작업은 직렬로 실행하세요. 서버 측 자동 재시도는 하지 않습니다.

## PR 목록 일괄 처리

0.5부터 `batch --prs`로 지정한 PR만 순서대로 처리합니다. 자동 감지·예약 실행·
일괄 게시 기능은 없습니다. 같은 주차의 명세 커밋, 평가표, 가산 정책, 모델 설정을
공유합니다. 가산 기준 인터뷰는 한 번 실행하여 `--policy` 파일로 전달하세요.
명세는 전체 40자리 커밋 SHA로 지정합니다. macOS/Linux에서 사용할 수 있습니다.

```bash
# 현재 세션에서 검토할 자료를 일괄 준비: 모델 호출 없음
uv run python -m ax_evaluator batch --prs PR_NUMBER_1 PR_NUMBER_2 \
  --week week-NN \
  --spec-ref FULL_SPEC_COMMIT_SHA \
  --rubric runs/assignment-rubric.json --policy runs/course-bonus-policy.json \
  --out runs/assignment-batch

# 별도로 선택한 모델에 평가를 맡기는 경우: 모델 호출과 결과 생성까지 진행
uv run python -m ax_evaluator batch --prs PR_NUMBER_1 PR_NUMBER_2 \
  --week week-NN \
  --spec-ref FULL_SPEC_COMMIT_SHA \
  --rubric runs/assignment-rubric.json --policy runs/course-bonus-policy.json \
  --config runs/provider.json --stage evaluate --out runs/assignment-model-batch
```

출력 폴더의 `summary.md`와 `summary.json`에 PR별 학번, 처리 상태, 기본점수,
가산점, 보류 수 및 상세 결과 경로가 모입니다. 미평가·실패 건은 0점으로 표시하지
않습니다. `complete`도 사람 검토용 초안 완료라는 뜻이며 확정 성적은 아닙니다.
`needs-review`는 검토 응답은 저장됐지만 증거·검토 등이 미완료인 상태입니다.

현재 세션/사람 검토는 각 `pr-N/packet-001/`의 양식을 작성하고 기존
`import-review` 명령으로 `pr-N/review.json`에 저장합니다. 예:

```bash
uv run python -m ax_evaluator import-review \
  runs/assignment-batch/pr-N/packet-001 runs/submission-review.json \
  --kind current-session --reviewer current-session-codex \
  --out runs/assignment-batch/pr-N/review.json
```

그 다음 처음의 `batch` 명령에 `--resume --stage finalize`를 추가하면 저장된
검토 응답을 일괄 확정합니다. `finalize` 단계는 GitHub·모델을 호출하지 않으며
검토 파일이 없는 PR은 `awaiting-review`로 남깁니다. 재시도 후 패킷 번호는 달라질
수 있으므로 실제 경로는 `progress.json`의 `packet`을 확인하세요. 위 경로의 `N`은 실제 PR 번호입니다.

중단 후에는 **같은 입력 명령에 `--resume`**을 붙입니다. 이미 완료한 PR과 수집한
스냅샷은 재사용합니다. 수집 후 PR이 변경되어도 이 묶음은 저장된 제출 커밋을
평가하며, 최신 제출을 평가하려면 새 묶음을 만드세요. 응답 없이 실패/중단된 모델
호출은 `--resume --retry-failed`를 명시할 때만 다시 시도합니다. 모델 응답 저장 후
결과 생성에서 실패했다면 응답을 재사용하므로 모델을 다시 호출하지 않습니다.
어떤 PR이 실패해도 나머지는 계속 처리하고, 실패가 남으면 종료 코드는 1입니다.

평가표·정책·모델 설정·입력 범위·하네스 코드가 바뀌면 같은 묶음의 재개를 거부합니다.
`prepare` 후 `evaluate`로 진행하려면 처음부터 같은 `--config`를 지정하세요.
모델 호출은 `evaluate` 단계에서만 일어납니다. 수정된 판단이나 기준으로 재평가할
때는 새 출력 폴더를 사용합니다. 원본·응답·실패 기록은 보존하고 진행 상태와
요약표만 갱신합니다. 동일한 묶음의 동시 실행은 잠금으로 막습니다.

## 다른 주차로 확장

과제마다 필수 요구사항과 판정 기준을 평가표에 정의합니다. 기본점수 분모는
그 필수항목 수이며, 과제가 다른 점수를 직접 비교하지 않습니다.
`scoring_policy`는 `requirements-only-v1`입니다. 평가 기준이나 응답 계약이
바뀌면 같은 원문으로 새 패킷을 준비하고 검토 응답도 갱신합니다.

특정 시점의 제출 자료를 평가하려면 `collect-pr --head-sha FULL_SHA`를 사용합니다.
지정한 커밋이 현재 PR head의 조상인지 확인하고 해당 커밋의 원문과 이력을 읽습니다.
이후 수정 내용이 들어갈 수 있는 현재 PR 본문은 과거 커밋 평가에서 제외합니다.
`--spec-ref`도 당시 upstream 명세 커밋으로 고정하세요. 그 명세 커밋에 학생 roster가
없으면 누락 상태를 기록하며 임의로 감점하지 않습니다. 과거 커밋 결과는 최신 head와
다르므로 게시 명령이 차단됩니다.

`rubrics/review-only.example.json`을 복사하고 주차·항목별 충족/감점 기준을 실제
과제 명세에 맞게 작성하세요. 예시는 공식 평가 기준이 아닙니다.

```bash
uv run python -m ax_evaluator collect-pr PR_NUMBER --week week-NN \
  --spec-ref FULL_SPEC_COMMIT_SHA --out runs/assignment-snapshot.json
uv run python -m ax_evaluator prepare runs/assignment-snapshot.json \
  --rubric runs/assignment-rubric.json --policy runs/course-bonus-policy.json \
  --out runs/assignment-packet
```

`checker: review-only`에서는 각 항목을 모델/사람이 검토합니다. 다른 과제의
결과 파일 규칙을 적용하지 않습니다. 전용 자동검사기를 추가할 때는
체크 ID·검사기·회귀 테스트를 함께 등록해야 합니다. PR 수집은 이 수업의
`weeks/week-NN`·`submissions/<학번>/week-NN` 레이아웃을 전제로 합니다.

## 결정적인 부분과 검증 한계

기본 제공 협상 실험용 검사기는 Python AST 구문 분석, 시나리오·CSV 데이터 계약, 고유 run 반복 수,
로그 파일 개수, `deal_possible`·`violation`·`correct` 재계산, 8수 상한을 확인합니다.
필수 검사와 추가 검사를 별도로 구현했으며 **공식 검사기와 동일한 결과를 보장하는
대체물은 아닙니다.** upstream 공식 검사기 원문은 스냅샷에 보관하지만 실행하지 않습니다.

`open`을 거래 불가능 시나리오의 정답으로 인정할지는 `open_non_deal` 정책입니다.
초깃값 `pending`은 해당 행에 대한 판단을 보류합니다. 교수자 결정 후 `correct`
또는 `incorrect`로 바꾸고 rubric version을 올리세요. 다른 명백한 불일치는 그대로
표시합니다. 크래시는 성공률 분자에 넣지 않으며 건수를 따로 표시합니다. 한 키의
완료 기록이 여러 개면 최종 기록을 임의 선택하지 않고 집계를 보류합니다.

로그 어댑터는 모든 결과 필드가 있는 JSON 행, `[result] { ... }`,
`{"type":"episode_result","result":{...}}`를 인식해 CSV와 비교합니다. 명시적인
`condition/repeat` 헤더와 `=== scenario ...:` 문맥을 가진 `[result] key=value`
형식도 지원합니다. 이 경우 로그 파일명과 헤더도 일치해야 하며 CSV에서 빠진
식별자를 가져와 맞추지는 않습니다. 이는
**에피소드 요약의 일치 검사**이며 도구 이벤트로부터 모든 카운터를 재구성한 검사가
아닙니다. 다른 텍스트 로그, CLI 이벤트, 호출·거부·회복의 의미는 근거 검토 항목으로
남깁니다. 파싱 실패를 학생 오류나 0건으로 바꾸지 않습니다. 추가 로그 어댑터는
실제 제출 형식을 보고 원문 위치를 보존하도록 구현해야 합니다.

모델 입력은 기본적으로 원문 문자 180,000자, 파일당 12,000자 안에서 앞·뒤 부분을
선택합니다. 행 번호·JSON 오버헤드를 포함한 실제 prompt 길이나 토큰 수의 상한은
아닙니다. `coverage.json`에 생략 범위를 명시하며, 패킷에 없는 행을 인용한 응답은
거부합니다. 충분한 문맥이 없으면 pending으로 남겨야 합니다. 더 큰 패킷은 다음처럼
새로 만들 수 있지만 모델의 context 한도와 비용은 별도로 확인해야 합니다.

```bash
uv run python -m ax_evaluator prepare runs/submission-snapshot.json \
  --rubric runs/assignment-rubric.json --policy runs/course-bonus-policy.json \
  --max-chars 500000 --per-file-chars 50000 --out runs/submission-expanded
```

스냅샷·패킷·규칙·구현·모델 설정의 해시를 기록합니다. 같은 입력과 같은 저장된
판정의 점수 계산은 동일하지만, 다른 모델이나 새로운 모델 호출의 판정이 같다고
보장하지 않습니다. 근거 위치가 존재한다고 주장 내용까지 맞는 것은 아닙니다.
최종 판단은 사람이 확인해야 합니다.

수집 한도는 파일당 8 MB, 전체 64 MB이며 초과 시 명시적으로 실패합니다. 심볼릭
링크는 읽지 않고, 알려진 키 패턴·환경변수 파일을 발견하면 저장을 중단합니다.
이 검사는 모든 비밀정보를 탐지하는 보장은 아니므로 외부 모델 호출 전에 수집한
원문을 확인하세요. Binary 파일은 해시·크기를 기록하고 텍스트 판단에서 제외합니다.

## 검증

```bash
uv run python -m unittest discover -s tests -v
```

테스트는 위반 은폐, 중복 반복, 크래시 보존, open 정책, 로그 불일치, 가짜 근거,
패킷 변경, 가산 상한, 모델의 자동 검사 덮어쓰기, CLI/API 요청·응답 처리를 검증합니다.
학생 코드 실행과 실제 모델 호출은 하지 않습니다.

회귀 테스트에는 시작 인터뷰, 명시 기준 배점, 중복 가산 방지, 경미한 결함의 감점
차단, 통과 항목의 수정 요청, 다른 주차의 review-only 평가, 코멘트 생성과 게시 제한도
포함합니다. GitHub 쓰기와 외부 모델 호출은 mock으로 검증하며 실제 게시하지 않습니다.
같은 입력·저장된 판정의 결과 재현성과 새 모델 판단의 일관성은 서로 다른 검증입니다.

이 디렉터리는 교수자용 하네스 추가로 승인받은 경로입니다. 기존 학생 제출용
ownership CI는 루트 경로 변경을 거부하므로 향후 PR에서 해당 실패가 예상됩니다.
이를 우회하려고 `scripts/`나 `.github/`를 변경하지 않았습니다.

## 연결 방식의 참고 문서

- [Codex 비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Claude Code 비대화형 실행](https://code.claude.com/docs/en/headless)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat)
- [Ollama OpenAI 호환 API](https://docs.ollama.com/api/openai-compatibility)
