# Round 3 runbook (jiseokkk)

Round 3: 11월 3일(화) 시작, 11월 17일(화) 23:59 종료. 제출(PR) 마감은 11월 23일(월) 23:59 (weeks/week-11/README.md).
이 폴더가 kit이 읽는 live 디렉터리다. 브랜치를 바꾸면 kit이 깨지므로 먼저 kit을 끈다.

## 시작 (한 번)

```bash
# 1. kit v4가 설치되어 있는지 (2026-11-03에 repo scripts/market_kit.py v4를 복사해 둠; v3는 market_kit.v3.py)
grep KIT_VERSION ~/Desktop/han/agent-market/market-kit/market_kit.py        # KIT_VERSION = "4"

# 2. join (같은 이름, 같은 키 -> 같은 참가자)
~/Desktop/han/agent-market/join.sh --background
tail -3 ~/Desktop/han/agent-market/join.log                                 # 첫 줄: [kit] market_kit 4, .../week-08

# 3. ACP checkout 안내가 열리는지 (가게 주소는 로그의 [shop] 줄 끝)
curl -s <가게 주소>/.well-known/acp.json | head -c 300

# 4. 자정 tuner cron (한 번만; 자동 설치는 권한 거부로 못 했음)
#    CLAUDE_CONFIG_DIR 필수: 이 서버의 claude 로그인은 ~/.claude-jiseok에 있어서, 없으면 "OAuth session expired"로 실패한다
#    (11-04 00:00 첫 실행이 그렇게 실패했고 값은 바뀌지 않았다). harness._env()가 기본값을 넣어 주지만 cron 줄에도 적는다.
( crontab -l 2>/dev/null | grep -v week-08/tune.py; echo '0 0 * * * cd /home/iai4/Desktop/han/ai-agent-engineering-101/submissions/26510129/week-08 && CLAUDE_CONFIG_DIR=/home/iai4/.claude-jiseok PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin /home/iai4/Desktop/han/ai-agent-engineering-101/.venv/bin/python tune.py >> logs/tune.log 2>&1' ) | crontab -
crontab -l
```

## 감시 (cron, 한 번만 설치)

11-11 15:37에 kit은 살아 있고 터널도 여기서는 응답했는데 시장의 `/api/shops`에서 내 가게가 빠져(alive=false)
buyer들이 가게를 못 받았다(open 90/h → 30/h). `watchdog.sh`가 10분마다 확인해 두 번 연속 빠지면 재접속한다.

```bash
chmod +x /home/iai4/Desktop/han/ai-agent-engineering-101/submissions/26510129/week-08/watchdog.sh
( crontab -l 2>/dev/null | grep -v watchdog.sh; echo '*/10 * * * * /home/iai4/Desktop/han/ai-agent-engineering-101/submissions/26510129/week-08/watchdog.sh >> /home/iai4/Desktop/han/ai-agent-engineering-101/submissions/26510129/week-08/logs/watchdog.log 2>&1' ) | crontab -
```

## 멈추기 / 다시 들어가기

```bash
kill $(cat ~/Desktop/han/agent-market/kit.pid)      # 멈추기
~/Desktop/han/agent-market/join.sh --background      # 다시 들어가기 (agent.json을 바꿨으면 반드시 재시작)
```

## 매일 확인

- `ps -p $(cat ~/Desktop/han/agent-market/kit.pid)`, `tail ~/Desktop/han/agent-market/join.log`
- `python status.py` — 한 화면: 지금 쓰는 점수식, 내 누적 점수, 메모리 크기, 오늘의 체결률과 점수, 조절값, 마지막 변경, 경보.
- `tail -2 logs/tune.log` — 자정 반성의 `[strategy] {"event": "tune", ...}` 한 줄. `changes`가 비어 있으면 그날은 변경 없음, `answered: false`면 모델이 답하지 않은 밤이다.
- `grep '"source": "alert"' .market/lessons.jsonl | tail -3` — 경보. 모델 호출 실패(6시간에 한 번), 답 없는 반성. 11-15 21:48에 구독 로그인이 만료된 것을 이틀 동안 아무도 몰랐다. 로그인이 끊겼으면 `claude auth login`(무인 실행용은 `claude setup-token`).
- `python3 -c "import json;p=json.load(open('.market/profiles.json'));print(p['formula'],p['saved'],p['hot_rows'],p['archived_rows'])"` — 지금 쓰는 점수식, 내 누적 점수, 최근 48시간 줄 수, 보관된 줄 수.
- `python3 -c "import json;print(json.load(open('.market/rulebook.json')))"` — 규칙 페이지 해시, 마지막 확인 시각, 바뀐 날과 바뀐 줄. `changed_at`이 새로 생겼으면 그날 밤 반성의 요약 맨 앞을 읽는다.
- `cat .market/strategy_params.json` — 지금 적용 중인 값. 지우면 기본값으로 돌아간다.
- `head -c 400 .market/ledger_stats.json` — restock이 60초마다 갱신하는 시장 체결 요약(SKU별 건수·중앙값). seller는 이 파일로 ask를 정한다. Round 3 첫 `settle` 항목이 ledger에 찍히면 `ledger.settles()`가 읽는 필드(`sku`/`item`, `price`)가 맞는지 한 번 확인한다.
- `grep skipped_below_cost logs/market-kit.log | tail -1` — 시장가가 원가 아래라 사지 않은 SKU. 같은 줄의 `stock_cost`/`stock_room`/`planned_cost`/`skus_too_dear`로 상한 상태를 본다.
- `python restock.py --state .market` — 지금 상태에서 뭘 살지 dry run (사지는 않음; ledger_cache.json을 갱신함).
- `.market/ledger_cache.json` — 최근 24시간 정산 캐시와 마지막 seq. 지우면 다음 실행이 5,000건부터 다시 받는다.
- `logs/market-kit.log`는 20MB마다 `.log.1`로 돌고 다음 회전 때 덮인다. 이틀에 한 번은 커밋한다.

## 수동 실행

```bash
python tune.py --summary-only          # 요약만 (토큰 0)
python tune.py --dry-run               # Opus 제안까지, 쓰지 않음
python tune.py                         # 제안을 검증해 .market/strategy_params.json에 씀
python tune.py --set buyer_kept_slack=-0.05 --reason "왜" --expect "어떤 숫자가 어디로"
                                       # 사람이 조절값을 바꿀 때: 같은 범위 검증, 같은 이력과 교훈 파일
```

## 메모리 파일 (`.market/`, 커밋하지 않음)

- `buyer_memory.json` 최근 48시간의 방문 줄, `buyer_memory.archive.jsonl` 그 이전(추가만 함), `profiles.json` 가게 프로파일.
- `profiles.json`을 지우면 다음 주문이 보관 파일에서 누적 계수를 다시 센다(약 1초). 보관 파일은 지우지 않는다.
- `buyer_memory.before-compaction-1117.json` — 11-17 19:37 첫 보관 직전의 전체 로그(103,368줄) 백업.
- `lessons.jsonl` 교훈과 경보. 없으면 `lessons_seed.jsonl`에서 시작한다.

## 공식 market test

test의 buyer는 가짜 가게 "Kit Shop North/South" 기록을 live 메모리에 남기므로 반드시 백업/복원한다.

```bash
kill $(cat ~/Desktop/han/agent-market/kit.pid)
cp .market/buyer_memory.json /tmp/bm.json; cp .market/shop_trades.json /tmp/st.json
../../../.venv/bin/python ../../../scripts/market_test_week07.py .
cp /tmp/bm.json .market/buyer_memory.json; cp /tmp/st.json .market/shop_trades.json
~/Desktop/han/agent-market/join.sh --background
```

## 종료 (11-17 23:59) 전후

- 매입은 마감까지 계속한다. 마지막 24시간에는 최근 6시간에 내 가게에서 팔린 카테고리와 마감 뒤에도 수명이 남는 식품만
  산다(`restock` 이벤트의 `liquidation` 문구). 11-17 저녁까지는 24시간 전에 전부 멈췄는데, 현금만 세던 보드의 규칙이었다.
- seller는 24시간 전부터 ask ≤ 시장가 −5%, 바닥은 원가(`regime` = sell-down). 원가 아래로는 팔지 않는다.
- board 스냅샷: `setsid nohup ./snapshot_board.sh > logs/board-snapshot.log 2>&1 < /dev/null &` (23:50, 23:58:30, 23:59:40,
  00:01, 00:10에 `logs/board-<시각>.json` 저장, 요약 한 줄씩).
- 마감 뒤: 시장은 11-24 19:00까지 멈춘다. kit은 `kill $(cat ~/Desktop/han/agent-market/kit.pid)`로 끈다.

## 그림

```bash
python make_figure.py          # architecture.tex 생성 + architecture-preview.png (matplotlib이 있을 때)
tectonic architecture.tex      # 또는 pdflatex, 또는 Overleaf에 올려 컴파일 -> architecture.pdf
```

이 서버에는 TeX 엔진이 없다(설치는 권한 거부). `architecture.pdf`는 다른 곳에서 `.tex`를 컴파일해 넣는다.

## 제출 (weeks/week-11/README.md)

```bash
git fetch upstream && git merge upstream/main
git push git@github.com:jiseokkk/ai-agent-engineering-101.git <브랜치>
# GitHub에서 Q00/ai-agent-engineering-101로 PR, 제목: [week-08] 26510129  (마감 11-23 23:59, PR을 연 시각 기준)
```

## forum

- `forum_post.py`가 restock 루프에서 5분마다 카테고리 하나의 시세 글을 buyer 명의로 씀 (`[strategy] market_post` 줄). 글/표 상태는 https://market.ourolabs.ai/forum.
- `tail -3 .market/outbox.sent.jsonl` — 보낸 글과 시장 응답(status 200이면 정상).

## 토큰

- 흥정: `claude -p --model haiku`(구독), 호출당 약 1,000자 입력, 가게가 3% 이상 움직일 때만(하루 900~1,100회), 실패 시 결정론적 fallback.
- 반성: `claude -p --model opus --effort high`, 하루 1회, 입력 약 8,400자.
- 모델이 답하지 않으면 buyer는 fallback 제안으로 계속 거래하고 로그에 `buyer_model_fallback`, 교훈 파일에 `alert`가 남는다.
