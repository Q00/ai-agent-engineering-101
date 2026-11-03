# Round 3 runbook (jiseokkk)

Round 3: 11월 3일(화) 시작, 11월 17일(화) 23:59 종료. 제출 마감 11월 17일 23:59.
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

## 멈추기 / 다시 들어가기

```bash
kill $(cat ~/Desktop/han/agent-market/kit.pid)      # 멈추기
~/Desktop/han/agent-market/join.sh --background      # 다시 들어가기 (agent.json을 바꿨으면 반드시 재시작)
```

## 매일 확인

- `ps -p $(cat ~/Desktop/han/agent-market/kit.pid)`, `tail ~/Desktop/han/agent-market/join.log`
- `tail -2 logs/tune.log` — 자정 tuner의 `[strategy] {"event": "tune", ...}` 한 줄. `changes`가 비어 있으면 그날은 변경 없음.
- `cat .market/strategy_params.json` — 지금 적용 중인 값. 지우면 기본값으로 돌아간다.
- `head -c 400 .market/ledger_stats.json` — restock이 60초마다 갱신하는 시장 체결 요약(SKU별 건수·중앙값). seller는 이 파일로 ask를 정한다. Round 3 첫 `settle` 항목이 ledger에 찍히면 `ledger.settles()`가 읽는 필드(`sku`/`item`, `price`)가 맞는지 한 번 확인한다.
- `grep skipped_below_cost logs/market-kit.log | tail -1` — 시장가가 원가 아래라 사지 않은 SKU.
- `logs/market-kit.log`는 20MB마다 `.log.1`로 돌고 다음 회전 때 덮인다. 이틀에 한 번은 커밋한다.

## 수동 실행

```bash
python tune.py --summary-only          # 요약만 (토큰 0)
python tune.py --dry-run               # Opus 제안까지, 쓰지 않음
python tune.py                         # 제안을 검증해 .market/strategy_params.json에 씀
```

## 공식 market test

test의 buyer는 가짜 가게 "Kit Shop North/South" 기록을 live 메모리에 남기므로 반드시 백업/복원한다.

```bash
kill $(cat ~/Desktop/han/agent-market/kit.pid)
cp .market/buyer_memory.json /tmp/bm.json; cp .market/shop_trades.json /tmp/st.json
../../../.venv/bin/python ../../../scripts/market_test_week07.py .
cp /tmp/bm.json .market/buyer_memory.json; cp /tmp/st.json .market/shop_trades.json
~/Desktop/han/agent-market/join.sh --background
```

## 토큰

- 흥정: `claude -p --model haiku`(구독), 호출당 약 860자 입력, 횟수 제한 없음, 실패 시 결정론적 fallback.
- tuner: `claude -p --model opus --effort high`, 하루 1회 약 1.3k 토큰.
- 구독 주간 한도에 걸리면 buyer는 fallback 제안으로 계속 거래하고, 로그에 `buyer_model_fallback`이 찍힌다.
