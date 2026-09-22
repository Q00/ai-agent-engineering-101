# Week 01 — 26510121 서찬일 (seochanit)

모임 비용을 읽고 정산한 뒤 기록하는 에이전트다. 기존 read_file, calculator에 write_note를 추가했다. 총액은 77600 KRW, 4명 균등 부담액은 19400 KRW이다. 도구 스키마와 구현은 first_agent.py, 설명 선택 근거는 TOOLS.md, 실제 시행착오는 PROCESS.md에 있다.

## 실행 환경

- 실제 기록된 환경: Windows PowerShell, Python 3.9.13, openai 2.48.0.
- 강의 안내는 Python 3.10 이상이다. 해당 버전에서의 실제 API 재현은 아직 수행하지 않았다. 이 차이를 숨기지 않고 제출한다.
- OpenRouter: https://openrouter.ai/api/v1
- 모델: nvidia/nemotron-3.5-lightning:free. 해당 모델로 성공한 실행 로그를 보존했다. 서비스의 향후 제공 여부나 동일 응답은 보장하지 않는다.
- 최대 반복 8회. temperature/seed는 지정하지 않아 서버 기본값을 사용한다. SDK의 기본 재시도/타임아웃 설정을 사용한다. A/B는 각 조건 1회이며 통계적 효과를 주장하지 않는다.
- 키를 제외한 설정은 아래에 명시했다. 키는 환경 변수로만 사용하고 파일에 저장하지 않는다.

## 새 PowerShell에서 재현

저장소 루트에서 시작한다. Python 실행 명령이 python인 환경을 기준으로 한다.

```powershell
cd submissions/26510121/week-01
python -m pip install -r requirements.txt
$secret = Read-Host "OpenRouter API key" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $secret).Password
Remove-Variable secret
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:AGENT_MODEL = "nvidia/nemotron-3.5-lightning:free"
python capture_run.py description-b
```

처음 checkout한 제출에는 settlement.txt가 없으며 기존 결과는 outputs/에 보관돼 있다. 실행 도우미는 settlement.txt가 이미 있으면 중단한다. 재실행하려면 이전 결과를 고유 이름으로 보존한다:

```powershell
if (Test-Path settlement.txt) {
    $archiveName = "outputs/settlement-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff').txt"
    Move-Item -LiteralPath settlement.txt -Destination $archiveName
}
```

출력 로그는 logs/에 고유 이름의 UTF-8 파일로 직접 저장된다. 마지막 종료 코드, 파일 존재 여부, 실제 파일 내용까지 포함한다. 로그를 편집하지 않는다. 직접 실행하는 경우 write_note는 기존 settlement.txt에 추가하므로 반복 실행 시 기록이 누적된다.

## 비교 실험 재현

각 실행 전 위 방법으로 settlement.txt를 보관해 파일이 없는 조건을 맞춘다.

```powershell
# 짧은 설명 A로 저장 요청
python capture_run.py three-tools
# 결과 보관 후, 설명 B로 동일한 저장 요청
python capture_run.py description-b
# 결과 보관 후, 명시적 저장 금지 요청을 A와 B 각각 실행
python capture_run.py no-save-ab
```

no-save-ab는 두 번의 별도 대화/API 실행이다. 불필요한 저장이 발생하면 로그에 기록하고 outputs/에 보관한 뒤 다음 조건을 실행한다. WRITE_NOTE_DESCRIPTION=A 또는 B로 설명만 바꾸며 기본값은 B다. capture_run.py는 레이블에 따라 자식 프로세스의 값을 지정한다. 정확한 요청 원문은 GOAL과 NO_SAVE_GOAL 및 실행 로그에 있다.

두 도구 기준 실행은 커밋 6ba7c2c의 starter 구현을 사용했고, 정산 입력은 73f83fb에서 마련했다. 현재 코드는 세 도구이므로 capture_run.py의 baseline 레이블을 사용한다고 두 도구로 돌아가지는 않는다. 과거 기준 실행 증거는 baseline 로그 세 개다.

## 관찰 결과

| 조건 | 호출 | 계산 | 저장 |
| --- | --- | --- | --- |
| 두 도구 기준 실행 | 읽기 → 계산 두 번 | 정확 | 도구 부재로 불가 |
| A 저장 요청 | 읽기 → 저장 | 정확 | 계산식 누락 |
| B 저장 요청 | 읽기 → 계산 두 번 → 저장 | 정확 | 계산식 포함 |
| A 명시적 저장 금지 | 읽기 | 정확 | 안 함 |
| B 명시적 저장 금지 | 읽기 | 정확 | 안 함 |

최종 기본 설명은 B다. 저장 금지에서 차이가 없었다는 결과도 보존한다. 인증 환경 변수 누락, cp949 출력 실패, UTF-8 설정 후 문자 깨짐과 직접 캡처 수정 과정은 로그/커밋에 남아 있다. 다른 도구 후보는 설계 검토만 했으며 실제 실패한 구현으로 주장하지 않는다. 사용자와 AI 도우미가 설계를 논의했고 AI 도우미가 코드·문서를 작성했으며 사용자가 실제 모델 실행을 수행했다.

## 검증 및 제출 한계

제출 폴더에서 `python verify_local.py`는 API 없이 쓰기·UTF-8 보존·A/B 스키마 차이를 검증한다. 저장소 루트에서:

```powershell
python scripts/check_week01.py submissions/26510121/week-01
```

현재 Python 3.9.13에서 두 검사 모두 통과했다. 구조 검사 통과는 성적이나 모든 환경의 재현을 보장하지 않는다. 원본 로그 7개와 출력 2개를 포함한다. API 키는 제출하지 않는다. roster #25가 upstream에 미병합이면 소유권 CI가 실패할 수 있다. 등록 병합 후 대상 브랜치 최신 상태를 반영하고 CI를 다시 확인해야 한다.
