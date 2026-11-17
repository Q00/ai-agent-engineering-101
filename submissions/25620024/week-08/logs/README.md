# logs/ — Round 3 기록과 복구 경위

키트의 `logs/market-kit.log`는 20 MB가 되면 `.1`로 넘어가고 한 개만 남긴다. Round 3는 11-03 21시에 시작했는데, 11-14 저녁에야 이 규칙을 알았고 그 전 회전분들은 이미 덮어써져 사라진 뒤였다. 그때 남아 있던 `.1`(11-14 06:12부터)을 먼저 복사하고, 남아 있는 모든 출처를 찾아 아래처럼 대체했다. 원본 텍스트는 손대지 않았고, 열 수 있게 gzip으로만 묶었다. 시간은 모두 KST다.

## 1. 키트 콘솔 로그 (원본 형식 그대로)

| 파일 | 구간 | 줄 수 | 출처 |
|---|---|---|---|
| `kept/market-kit.0612-1114-1532.log` | 11-14 06:12:29 → 11-15 00:32:58 | 149,643 | 회전된 `.1`을 사라지기 전에 손으로 복사 |
| `kept/market-kit.0032-1115-1208.log` | 11-15 00:32:58 → 21:08:58 | 143,919 | VPS cron(`private/keep_log.sh`, 10분)이 새 `.1`을 자동 보관 |
| `kept/market-kit.2108-1116-0556.log` | 11-15 21:08:59 → 11-16 14:56:16 | 145,451 | 〃 |
| `kept/market-kit.1456-1116-2352.log` | 11-16 14:56:17 → 11-17 08:52:17 | 151,493 | 〃 |
| `market-kit.log` | 11-17 08:52:17 → 복사 시점 | 82,249 (11-17 20:13 복사) | 키트가 쓰는 현재 파일. Round 3 종료(23:59) 뒤 최종본으로 다시 복사한다 |
| `recovered/market-kit.journal-1108.log.gz` | 11-08 12:06:00 → 23:59:59 | 92,737 | systemd journald |
| `recovered/market-kit.journal-1109.log.gz` | 11-09 하루 | 178,678 | 〃 |
| `recovered/market-kit.journal-1110.log.gz` | 11-10 하루 | 191,926 | 〃 |
| `recovered/market-kit.journal-1111.log.gz` | 11-11 하루 | 187,273 | 〃 |
| `recovered/market-kit.journal-1112.log.gz` | 11-12 하루 | 177,548 | 〃 |
| `recovered/market-kit.journal-1113.log.gz` | 11-13 하루 | 202,269 | 〃 |
| `recovered/market-kit.journal-1114.log.gz` | 11-14 00:00:00 → 06:12:28 | 53,760 | 〃 (여기서 `kept/` 파일로 이어짐) |
| `recovered/market-team.journal-1108-1115.log.gz` | 11-08 → 11-15 | 11,016 | 팀 회의 서비스(marketing_agent.py)의 journald |

`kept/` 파일 이름은 `market-kit.<첫 줄 시각 HHMM, KST>-<회전 시각 mmdd-HHMM, VPS 시계 UTC>.log`이다. 다섯 파일은 끊김 없이 이어진다(앞 파일의 끝 줄 = 뒤 파일의 첫 줄 시각).

키트 버전 확인(`market_kit 4`): 회전 때문에 제출한 `market-kit.log`의 첫 줄은 키트 시작 줄이 아니다. week-08 폴더로 처음 join한 시작 줄은 `recovered/transcript-excerpts-1103-1107.log` 6번째 줄 `2026-11-03 21:03:48 [kit] market_kit 4, .../submissions/25620024/week-08`에 있다. 그 뒤 재시작한 시작 줄은 `kept/market-kit.0612-1114-1532.log` 80,154번째 줄(`15:56:58 [kit] market_kit 4, ...`)에 남아 있다.

journald 복구 방법: market-kit.service는 stdout을 파일과 journald 양쪽에 쓴다. `journalctl -u market-kit.service -o cat --since ... --until ...`로 하루씩 뽑으면 키트 로그와 같은 줄이 나온다. 검증: journald 11-14분과 `kept/` 파일이 겹치는 구간을 비교했더니, 줄 끝 공백 5줄을 빼면 똑같았다. 이 VPS의 journald는 7일치만 보관한다(`MaxRetentionSec=7day`). 그래서 복구하던 11-15에는 11-08 12:06 이후만 남아 있었고, 그 전은 이 방법으로 살릴 수 없었다. 이 창은 매일 하루씩 밀려나므로, 위 파일들은 지워지기 전에 뽑아 둔 것이다.

## 2. 11-03 ~ 11-08 공백을 메우는 기록

이 구간의 키트 콘솔 텍스트는 남아 있지 않다. 대신 거래 자체는 다른 경로로 전부 남아 있다.

시장 원장 사본 (`private/market.db`, 5분마다 공식 `/api/ledger`를 동기화한 것) → CSV:

| 파일 | 행 수 | 구간 | 내용 |
|---|---|---|---|
| `recovered/ledger-shop-settles-r3.csv.gz` | 28,882 | 11-03 21:02:48 → 11-15 08:30 | dragonash 가게가 판 모든 정산 |
| `recovered/ledger-buyer-settles-r3.csv.gz` | 20,640 | 11-03 21:05:10 → 11-15 08:45 | dragonash 구매자가 산 모든 정산 |
| `recovered/ledger-shop-supplies-r3.csv.gz` | 6,415 | 11-03 23:23:38 → 11-15 08:27 | 가게의 공급 매입 |
| `recovered/shop-quotes-r3.csv.gz` | 109,507 | 11-01 00:49 → 11-15 10:21 | 가게 점원이 낸 모든 견적(등급, 실험 arm, 호가, 결과). 11-03 21시 이전 행은 Round 2 끝자락 |

키트가 직접 쓰는 기록 파일 (`recovered/kit-records/`):

| 파일 | 줄 수 | 구간 | 내용 |
|---|---|---|---|
| `payments.jsonl.gz` | 25,334 | Round 3 전체 (11-17 07:45까지) | 구매자의 모든 결제. 시간 필드는 없고 `ledger` 번호로 원장 CSV와 이어진다 |
| `outbox.sent.jsonl.gz` | 122,740 | Round 3 전체 (11-17 07:45까지) | 게시판에 보낸 글과 투표 전부. `refs`에 근거 원장 번호 |
| `broken.jsonl.gz` | 155 | 11-04 11:55 → 11-17 | 산 물건이 고장 난 기록 |
| `reserve.jsonl.gz` | 46,185 | 11-03 21:02 → 11-17 | 가게가 재고를 잡아둔 기록 |
| `hr-history.jsonl.gz` | 318 | 11-04 01:49 → 11-17 | 인사팀 KPI 채점과 보상·강등 이력 |
| `model_usage.jsonl.gz` | 791 | 11-07 17:01 → 11-17 | 팀 회의별 모델 호출 (어느 모델, 역할, 토큰) |
| `price_ab.jsonl.gz` | 4,392 | 11-11 08:26 → 11-17 | 가격 A/B 실험 결과 |
| `hq_decisions.md` | — | 11-03 → 11-17 | HQ 결정 기록 |

그 밖에 `recovered/transcript-excerpts-1103-1107.log`는 이 기간 작업하면서 Claude Code 세션 기록에 찍혀 있던 키트 로그 줄 449개를 모은 것이다(11-03 56줄, 11-04 259줄, 11-05 24줄, 11-07 110줄). 원래 순서대로 중복만 뺐고, 키나 토큰이 섞인 줄은 넣지 않았다.

## 3. 그래도 남는 공백

- 11-03 21:00 → 11-08 12:06 사이의 키트 콘솔 텍스트(협상 대화, 거절 사유 문장)는 위 발췌 449줄 말고는 없다. 이 구간의 거래 숫자는 원장 CSV와 kit-records로 전부 확인할 수 있지만, 협상 문장은 볼 수 없다.
- 이 구간의 거래는 시장 서버의 공식 원장(`/api/ledger`)에도 그대로 있으니, 위 CSV는 공식 원장과 대조할 수 있다.

## 4. 이 폴더의 다른 파일

`buyer-under-test-*`, `seller-under-test-*` 등은 키트 셀프 테스트 로그이며 위 복구와는 상관없다.

교훈: 로그 회전 규칙은 라운드 시작 전에 확인했어야 했다. 지금은 `private/keep_log.sh`가 10분마다 새로 회전된 파일을 `logs/kept/`에 보관한다.
