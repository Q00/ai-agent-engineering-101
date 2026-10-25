# STEP 8 — 실행 설정과 실제 seller/buyer 연결

## 세 가지 실행 방식

`profiles/`에는 세 가지 방식에 해당하는 네 조합을 저장했다.
`agent.json`의 기본 선택은 Claude끼리 거래하는 `claude`다.

| 방식 | 프로필 | seller | buyer |
|---|---|---|---|
| Claude 통일 | `claude` | Claude Code + Haiku | Claude Code + Haiku |
| Codex 통일 | `codex` | Codex + Luna | Codex + Luna |
| 개별 선택 | `mixed-claude-seller` | Claude Code + Haiku | Codex + Luna |
| 개별 선택 | `mixed-codex-seller` | Codex + Luna | Claude Code + Haiku |

실제 모델 ID는 Haiku의 CLI 별칭 `haiku`와 Luna의 `gpt-6-luna`를 사용한다.
각 프로필에는 양쪽 하네스·모델과 실행 명령을 명시했다.

## agent.json 선택

과제 폴더에서 원하는 설정을 선택한다.

```powershell
cd C:\Submissions\ai_agent_engineering\submissions\24522014\week-06
.\a2a-lab\.venv\Scripts\python.exe agent_profiles.py --profile claude
```

`--profile`에 `codex`, `mixed-claude-seller`, `mixed-codex-seller`도 사용할 수 있다.
선택 도구는 `agent.json`만 변경한다. 기존 보고서와 로그는 수정하지 않는다.
실행 중인 서버를 바꾸거나 명령줄에 직접 적은 `--mode`를 바꾸는 기능은 아니다.
기존 공식 테스트 결과가 있다면 설정 변경 후 전체 테스트를 다시 실행해야
새 `agent.json`의 명령과 일치하는 결과를 얻는다. 재실행 전 이전 결과 보존은
다음 단계에서 다룬다.

기본 설정의 명령은 다음과 같다.

```json
{
  "seller": {"cmd": "python seller.py --mode claude --port {port}"},
  "buyer": {"cmd": "python buyer.py --mode claude --url {url} --item {item} --budget {budget}"}
}
```

이 예시는 명령 부분만 발췌했다. 실제 파일에는 profile, harness와 각 모델도 있다.
`{port}`, `{url}`, `{item}`, `{budget}`은 공식 테스트가 채우는 자리 표시자다.
명령은 과제 폴더를 작업 디렉터리로 사용한다. `python`은 의존성이 설치된
가상환경의 Python을 가리켜야 한다. 공식 테스트를 실행하는 터미널에서는
가상환경을 활성화하거나 다음처럼 현재 세션 PATH 앞에 추가한다.

```powershell
$env:Path = (Resolve-Path .\a2a-lab\.venv\Scripts).Path + ";" + $env:Path
python --version
```

## 직접 연결하기 — Claude 통일

두 터미널 모두 과제 폴더에서 시작한다. 10099 포트의 이전 서버는 해당
서버 터미널에서 Ctrl+C로 종료한 뒤 새 seller를 켠다.

터미널 1:

```powershell
.\a2a-lab\.venv\Scripts\python.exe seller.py --mode claude --port 10099
```

터미널 2:

```powershell
.\a2a-lab\.venv\Scripts\python.exe buyer.py --mode claude --url http://127.0.0.1:10099 --item lamp --budget 120
```

이제 상대도 하네스 기반 가게다. `receipt.seller`가 `Harness Furniture Shop`인지,
가격이 100 이상 120 이하인지 확인한다. `Kit Shop`은 이전 단계의 규칙 기반
가게 이름이므로 실행 대상 URL도 함께 확인한다.

예산 부족 상황:

```powershell
.\a2a-lab\.venv\Scripts\python.exe buyer.py --mode claude --url http://127.0.0.1:10099 --item bookshelf --budget 230
```

구매하지 않고 끝나며 receipt는 null이어야 한다. buyer가 먼저 철수하면
`walked_away`, seller가 먼저 거절하면 `seller_rejected`다. 둘 다 정상적인
거래 불성립이며, `failed`나 시간 초과는 정상 거래 결과로 간주하지 않는다.

## 직접 연결하기 — 교차 조합

앞 seller를 Ctrl+C로 종료한 뒤 새로 실행한다.

Claude seller / Codex buyer:

```powershell
# 터미널 1
.\a2a-lab\.venv\Scripts\python.exe seller.py --mode mixed --seller-harness claude --port 10099
# 터미널 2
.\a2a-lab\.venv\Scripts\python.exe buyer.py --mode mixed --buyer-harness codex --url http://127.0.0.1:10099 --item lamp --budget 120
```

Codex seller / Claude buyer:

```powershell
# 터미널 1: 앞 seller 종료 후
.\a2a-lab\.venv\Scripts\python.exe seller.py --mode mixed --seller-harness codex --port 10099
# 터미널 2
.\a2a-lab\.venv\Scripts\python.exe buyer.py --mode mixed --buyer-harness claude --url http://127.0.0.1:10099 --item lamp --budget 120
```

Codex 통일은 양쪽 모두 `--mode codex`를 사용한다.
실습 중에는 양쪽 모델이 판단할 때마다 사용량이 소모된다. 종료할 때는
서버 터미널에서 Ctrl+C를 누른다.

## 설정 기반 자동 연결 검사

`verify_step8.py`는 프로필의 실행 명령을 읽어 양쪽 프로세스를 직접 시작한다.
프로필 명령의 `python`은 검사를 실행한 가상환경의 인터프리터로 해석한다.
셸 문자열로 실행하지 않으며 값을 인자에 넣기 전에 명령 템플릿을 분리한다.
매번 빈 포트에서 자기 seller만 시작·종료하고 agent.json 선택은 바꾸지 않는다.

```powershell
.\a2a-lab\.venv\Scripts\python.exe verify_step8.py --profile mixed-claude-seller
# 네 조합 전체를 순서대로 검증하려면
.\a2a-lab\.venv\Scripts\python.exe verify_step8.py --profile all
```

각 조합에서 lamp 예산 120과 bookshelf 예산 230을 한 번씩 확인한다.
반복 공식 trade test와 구분되는 통합 실행 검사다.
원본 카드, buyer 출력, seller 출력과 프로필·명령·결과는
`logs/step8/<실행시각>-<프로필>/`에 저장된다.

직접 읽을 코드:

- `agent_profiles.py`: 프로필 계약 검사, 선택, 실제 프로세스 인자 구성.
- `profiles/*.json`: 네 조합과 명령의 차이.
- `verify_step8.py`: 해당 명령으로 두 에이전트를 연결하고 영수증·예산 검증.

로컬 설정 검사 4개가 통과했다. 하네스 선언과 명령의 선택 일치, 자리 표시자,
모델 정보, 인자 보존과 설정 변경 시 기존 증거를 수정하지 않는 것을 확인했다.
자료는 `logs/step8-tests/`와 `logs/step8-tests-20261016-154037.stderr.txt`에 보존했다.

```powershell
.\a2a-lab\.venv\Scripts\python.exe -m unittest test_agent_profiles -v
```

## 실제 연결 결과

네 조합 모두 통과했다. 조합마다 두 시나리오를 실행하여 총 8건을 확인했다.

| 프로필 | lamp 예산 120 | bookshelf 예산 230 |
|---|---|---|
| `claude` | 120 구매, 3턴 | seller 거절, 영수증 없음 |
| `codex` | 120 구매, 3턴 | seller 거절, 영수증 없음 |
| `mixed-claude-seller` | 120 구매, 3턴 | seller 거절, 영수증 없음 |
| `mixed-codex-seller` | 100 구매, 1턴 | seller 거절, 영수증 없음 |

bookshelf의 seller 하한가는 260이므로 예산 230에서 거절된 것은 정상이다.
모델의 판단에 따라 재실행 시 가격과 턴 수는 달라질 수 있다.
이 결과는 조합당 한 번의 연결 검사이며, 반복 실행 안정성은 STEP 9에서 확인한다.

원본 증거 디렉터리:

- `logs/step8/20261016T065600466885Z-claude/`
- `logs/step8/20261016T065619079179Z-codex/`
- `logs/step8/20261016T065813436261Z-mixed-claude-seller/`
- `logs/step8/20261016T065824791938Z-mixed-codex-seller/`

각 디렉터리의 `verification.json`에 실제 실행 명령과 결과가 있다.
이번 검증에서 `agent.json`의 기본 선택은 `claude`로 유지했다.
최종 제출용 전체 trade test는 STEP 9, REPORT.md는 STEP 10에서 진행한다.
