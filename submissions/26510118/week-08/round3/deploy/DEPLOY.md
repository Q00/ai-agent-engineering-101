# 서버 배포 안내 (Round 3)

Azure 서버(`4.230.8.44`, 사용자 `azureuser`)에서 키트와 분석 실행기를 서비스 두 개로 돌립니다.
(직접)은 사용자가, (Claude)는 Claude가 하는 단계입니다. 명령은 한 블록에 하나씩입니다.

| 서비스 | 하는 일 | 설정 파일 |
|---|---|---|
| `kiro-kit` | 키트: 가게(seller, restock)와 buyer(market_buyer, review), 터널 | `deploy/kiro-kit.service` |
| `kiro-analysis` | 분석 실행기: 장부 수집, 시장 지표·답글, 3시간·12시간 분석 | `deploy/kiro-analysis.service` |

서버의 폴더 구조:
```
/home/azureuser/market/
  .venv/              이미 있음 (a2a-sdk, uvicorn, httpx, cryptography). 자료실용 mcp==1.30.0은 3번 단계에서 추가
  market_kit.py       키트 v4 (저장소의 scripts/market_kit.py)
  round3/             우리 코드 (저장소의 submissions/26510118/week-08/round3)
    shop_key.pem, buyer_key.pem   시장에 Kiro729로 등록된 키 (커밋하지 않음)
    .market/          키트와 우리 프로그램이 쓰는 파일 (자동 생성)
    logs/market-kit.log, logs/analysis.log   제출할 로그
  openai.env          OPENAI_API_KEY=... (비밀)
  owner.env           MARKET_OWNER=... claim code (비밀)
```

## 1. 파일 복사 (직접, PC의 PowerShell, 저장소 폴더에서)

```powershell
cd C:\Users\bubuj\Documents\ai-agent-engineering-101
```

```powershell
scp -i $HOME\.ssh\kiro_azure scripts\market_kit.py azureuser@4.230.8.44:~/market/
```

```powershell
scp -i $HOME\.ssh\kiro_azure -r submissions\26510118\week-08\round3 azureuser@4.230.8.44:~/market/
```

```powershell
scp -i $HOME\.ssh\kiro_azure submissions\26510118\week-08\shop_key.pem submissions\26510118\week-08\buyer_key.pem azureuser@4.230.8.44:~/market/round3/
```

## 2. 비밀값 입력 (직접, 서버에서)

```powershell
ssh -i $HOME\.ssh\kiro_azure azureuser@4.230.8.44
```

OpenAI 키 (화면에 보이지 않게 입력됩니다):
```bash
read -s -p "OpenAI API key: " K && umask 077 && printf 'OPENAI_API_KEY=%s\n' "$K" > ~/market/openai.env && unset K && echo saved
```

claim code (https://market.ourolabs.ai/join 에서 복사):
```bash
read -s -p "claim code: " C && umask 077 && printf 'MARKET_OWNER=%s\n' "$C" > ~/market/owner.env && unset C && echo saved
```

## 3. 자료실(MCP) 패키지 설치와 모델 호출 확인 (직접 또는 Claude, 서버에서)

자료실 서버(`data_room.py`)가 쓰는 MCP 공식 패키지입니다. PC 시험과 같은 판으로 고정합니다:
```bash
~/market/.venv/bin/pip install "mcp==1.30.0"
```

자료실이 켜지는지 확인합니다. 툴 4개 이름이 나오면 성공입니다:
```bash
cd ~/market/round3 && MARKET_DIR=~/market/round3/.market ~/market/.venv/bin/python -c "import mcp_host; r = mcp_host.DataRoom('integrated').__enter__(); print([t['name'] for t in r.tools]); r.__exit__(None, None, None)"
```

모델 호출 확인:

```bash
cd ~/market/round3 && set -a && . ~/market/openai.env && set +a && ~/market/.venv/bin/python -c "import harness as h; print([h.ask('Reply with OK', m) for m in (h.MODEL, h.MODEL_MID, h.MODEL_STRONG)])"
```

세 칸 모두 `OK`가 나오면 성공입니다. 빈 칸이 있으면 그 모델 이름이 API에서 안 되는 것이니 Claude에게 알려 주세요(`OPENAI_MODEL...` 환경 변수로 바꿀 수 있음).

## 4. 설치와 시작 (직접 또는 Claude, 서버에서)

```bash
bash ~/market/round3/deploy/install.sh
```

빠진 파일이 있으면 `missing: ...`이라고 알려 주고 멈춥니다.

## 5. 확인

```bash
bash ~/market/round3/deploy/check.sh
```

| 볼 것 | 성공 모습 |
|---|---|
| 키트 로그 첫 줄 | `[kit] market_kit 4, /home/azureuser/market/round3` |
| 등록 | `[market] shop registered as Kiro729, round N`, `[market] buyer registered as Kiro729, round N` |
| Round 3가 열린 뒤 | `[supply] ...` (재고 구매), `[acp] settled ...` (가게 정산), `[acp] paid ...` (buyer 결제) |
| 분석 로그 | `[analysis] collect: ...`, 정산이 20건 넘으면 `[analysis] index: market index #1 posted` |

## 6. 코드를 고칠 때 (PC에서 고치고 다시 복사)

| 고친 파일 | 반영 방법 |
|---|---|
| `market_buyer.py`, `restock.py`, `review.py`, `pricing.py`, `buyer_memory.py` | 복사만 하면 다음 실행부터 반영 (키트가 훅을 매번 새로 실행) |
| `seller.py` | 복사 뒤 서버에서 `pkill -f seller.py` → 키트가 60초 안에 다시 띄움 |
| `analysis.py`, `analysts.py`, `context.py`, `insights.py`, `forum.py`, `tools.py`, `data_room.py`, `mcp_host.py`, `ledger.py` | 복사 뒤 `sudo systemctl restart kiro-analysis` (자료실은 분석마다 새로 켜지므로 따로 재시작할 것 없음) |
| `agent.json` | 복사 뒤 `sudo systemctl restart kiro-kit` (터널 주소가 바뀌어도 시장이 기록을 옮겨 줌) |

파일 하나만 복사하는 예:
```powershell
scp -i $HOME\.ssh\kiro_azure submissions\26510118\week-08\round3\market_buyer.py azureuser@4.230.8.44:~/market/round3/
```

## 7. (선택) Claude Code에서 자료실 쓰기

자료실은 표준 MCP 서버라 Claude Code도 붙일 수 있습니다. PC에서 한 번 등록하면, Round 3 중에 "어제 김치가 얼마에 팔렸지?", "Beta 가게 믿을 만해?"를 Claude Code에게 바로 물어볼 수 있습니다(ssh로 서버의 자료실을 켭니다):
```powershell
claude mcp add kiro-data-room -- ssh -i $HOME\.ssh\kiro_azure azureuser@4.230.8.44 "cd ~/market/round3 && MARKET_DIR=~/market/round3/.market ~/market/.venv/bin/python data_room.py --as integrated"
```
이렇게 연결하면 메모는 "통합 분석"의 이름으로 남으니, 메모 남기기는 쓰지 않는 게 좋습니다.

## 8. 2차 고도화 때 (하루 뒤)
`round3/strategy.json`의 `"phase"`를 `"focus"`로 바꾸면 통합 분석이 12시간마다 규칙을 실제로 바꾸기 시작합니다(그 전에는 "채택했을 것"만 기록). 숫자 조정은 데이터를 보고 함께 정합니다.

## 9. 끝나면 (11/18)
1. (직접) 로그를 PC로 가져오기:
```powershell
scp -i $HOME\.ssh\kiro_azure azureuser@4.230.8.44:~/market/round3/logs/market-kit.log submissions\26510118\week-08\logs\market-kit.log
```
2. (직접) 서비스 멈추기: `sudo systemctl disable --now kiro-kit kiro-analysis`
3. (직접) Azure 포털에서 **리소스 그룹 `kiro-market`을 통째로 삭제** (VM만 지우면 공인 IP와 NIC가 남아 과금)
