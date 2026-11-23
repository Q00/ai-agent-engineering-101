# week-08 (중간 평가) — yeonsong2의 Round 3 가게·buyer

설명은 `REPORT.md`, 구조는 `architecture.pdf`. 이 파일은 **돌려 보는 법**만 적는다.

```bash
python -m pip install a2a-sdk httpx cryptography uvicorn starlette   # 키트와 같은 의존성
python test_hooks.py        # 훅·규칙 전부 오프라인 자체 검사 (LLM은 review 한 번만 부름; 없으면 코드 fallback)
python metrics.py           # REPORT 3부의 표를 logs/ 와 .market/ 에서 다시 만든다 (--offline: 보드 조회 생략)
python tuner.py --dry       # meta-harness가 지난 1시간 로그에서 재는 지표
```

로컬 end-to-end (시장 없이, 가게 ↔ buyer A2A + 영수증 서명):

```bash
MARKET_DIR=/tmp/m/.market python seller.py --port 8979 &      # inventory.json / catalog.json / order.json은 test_hooks.py 참고
python market_buyer.py --shops shops.json --item lamp --budget 120 --order /tmp/m/.market/order.json
```

로그: `logs/market-kit.log`(마지막 45 MB), `logs/market-kit-full.log.gz`(회전 스냅숏을 이어 붙인 전체), `logs/tuner.log`.

라이브 (Round 3 동안 실제로 돌린 방법): `./run_kit.sh --background` (claim code는 `~/.market_owner`), `./watchdog.sh`.
키는 `shop_key.pem`/`buyer_key.pem`(커밋하지 않음). 키트는 `market_kit.py`(v4에 `pay` 훅, 터널 재시도, outbox 상한, ACP 카드 JSON Schema 패치).

| 파일 | 역할 |
|---|---|
| `seller.py` | A2A 가게. 규칙 기반 호가(`price_move`, `floor_price`), 단위 선택(`find_unit`), 영수증 서명 |
| `market_buyer.py` | 주문마다: 가게 거르기·고르기, SKU 선택, `accept` 정책, 게시판 투표, 메모리(`buyer_memory.json`) |
| `buyer.py` | A2A 협상 클라이언트(첫 수는 시장가 규칙, 이후 LLM) |
| `acp_buyer.py` | `pay` 훅: 가게 acp.json에서 툴 생성(스키마 검증) → LLM 호출 순서 → 검사 → AP2 서명 → 결제 |
| `review.py` | 정산 후 별점, 메모리 갱신(거짓 주장·보증), ledger 참조 구매 기록 게시 |
| `restock.py` | 60초마다: ledger 정산 매출 기준 매입, 급등 SKU 회피, 1시간마다 호가표 게시, tuner 기동 |
| `tuner.py` | meta-harness: 1시간마다 로그 지표 → LLM이 손잡이 3개를 범위 안에서 조정 → `params.json` |
| `market_price.py` | 공개 ledger `settle` 항목 캐시, SKU별 중앙값 |
| `harness.py` | Claude Code headless 호출, JSON 파싱 |
