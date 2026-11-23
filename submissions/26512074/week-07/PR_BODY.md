## What I built

6주차 가구 에이전트에 정수 가격의 Ed25519 영수증 서명과 후기·거래 기억을 활용하는 시장 구매자를 추가했습니다. 실제 시장에서 자기 상점의 성과와 후기를 읽어 호가를 조정하고, 부풀린 영수증이 확인된 상대를 이름으로 피하도록 구현했습니다.

## What I tried and discarded

- A2A의 숫자가 실수로 전달되는 점을 고려해 서명·검증 모두 가격을 정수로 정규화했습니다. 소수 가격을 임의로 반올림하는 방식은 사용하지 않았습니다.
- 첫 배포에서 공식 키트가 cloudflared 출력을 cp949로 읽다가 실패했습니다. 공용 파일을 수정하지 않고 운영 프로세스를 UTF-8 모드로 시작하도록 고쳤으며 실패 로그를 보존했습니다.
- 시장의 악평을 사실로 단정하지 않고 검증용 거래 한 번으로 확인했습니다. maple-home은 제안 130에 영수증 145를 반환했고, 구매자는 예산 150 안이더라도 마지막 승인 가격을 넘는 영수증을 거부했습니다. 이후 실제 구매에서 해당 이름을 제외합니다.
- 자기 상점의 실제 인정 거래와 후기를 읽은 모델이 최초 호가 가이드 마진을 20%에서 15%로 변경했습니다. 변경 전후 인정 거래 수는 2→9, 누적 평균 거래가는 142.50→176.33이었으며 품목 구성이 섞인 누적 수치이므로 인과 효과로 단정하지 않았습니다.
- 이번 가입은 Round 2부터이므로 Round 0·1에 참여한 것처럼 기록하지 않았습니다. Codex의 구현·검증 도움과 원본 실행 기록도 보고서에 명시했습니다.

## How to run

Python 3.12와 로그인된 Codex CLI를 사용합니다. 모델은 `gpt-5.6-terra`, 추론 수준은 `low`입니다. 저장소 루트의 PowerShell에서 실행합니다.

```powershell
python -m venv submissions/26512074/week-07/.venv
submissions/26512074/week-07/.venv/Scripts/python.exe -m pip install -r submissions/26512074/week-07/requirements.txt
$env:PATH = (Resolve-Path submissions/26512074/week-07/.venv/Scripts).Path + ';' + $env:PATH
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
python scripts/market_test_week07.py submissions/26512074/week-07
python scripts/check_week07.py submissions/26512074/week-07 --github totato2203
```

공식 시장 테스트는 25/25 통과했고, 후기 비교는 좋은 상점을 3회 모두 선택했습니다. 실제 시장에서 `totato2203`의 가게와 구매자가 GitHub 계정에 연결됐으며, 구매자의 Round 2 양쪽 서명 거래가 인정됐습니다. 현재 PC에서 공식 키트와 공개 터널을 숨김 프로세스로 실행합니다. 마감까지 PC를 켜 두어야 하며, 배포 재시작 절차와 필요한 로컬 비밀 파일 설정은 제출 폴더의 README.md에 있습니다. 개인키와 claim code는 커밋하지 않았습니다.

## Checklist

- [x] `python scripts/check_week07.py submissions/26512074/week-07 --github totato2203` 로컬 검사 통과
- [x] 공식 시험·실제 시장·필수 도전 실행 로그를 `logs/` 아래에 커밋
- [x] 변경 내용에 API 키·개인키·claim code 없음
- [x] 커밋을 스쿼시하거나 기존 기록을 재작성하지 않음
