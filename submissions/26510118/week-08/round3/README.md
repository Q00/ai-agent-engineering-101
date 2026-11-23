# round3: Round 3에서 실제로 돌린 에이전트

11/3부터 11/17 23:59까지 Azure VM에서 systemd 서비스 두 개로 돌렸다. `kiro-kit`은 키트
(`scripts/market_kit.py`, 고치지 않음)를 `agent.json`의 훅으로 돌리고, `kiro-analysis`는 분석 실행기
`analysis.py`를 돌린다. 구조 그림은 `../architecture.pdf`, 설계 설명은 `../REPORT.md` 1·2부에 있다.

## 모듈 지도

거래 계층 (키트가 부르는 훅, 코드가 먼저)

| 파일 | 하는 일 |
|---|---|
| `agent.json` | 키트 훅: seller, market_buyer, restock, review |
| `seller.py` | A2A 가게. 요청을 읽고 단위를 고르고, 첫 호가와 하한 사이에서 흥정하고, 서명한 영수증을 낸다 |
| `pricing.py` | 원가에서 하한, 등급별 보증 가산, 가격 인하 |
| `restock.py` | 20분마다 보충: 수요에 맞춘 진열과 고가 라인 |
| `market_buyer.py` | 주문마다 실행: 후보 가게 점수, 세 곳 동시 견적, 마진 몫 하한 아래로는 사지 않는 흥정, 등급과 보증과 유통기한 조건 |
| `buyer_memory.py` | 가게별 notebook (코드만 쓴다) |
| `review.py` | 정산 영수증으로 별점 |
| `margin.py` | Patch 3.6의 saved(가게 마진 중 buyer가 가진 몫)를 등급별 원가 분포로 기대값 계산 |
| `breakage.py` | 등급별 고장률 표 (가게와 buyer가 함께 씀) |
| `common.py`, `contract.py` | 공용 함수, 파일마다 누가 쓰고 누가 읽는지 |
| `harness.py` | 모델의 유일한 문: Responses API와 Decisions API, 호출 기록, 실패하면 빈 답 |

빠른 루프 (`analysis.py`가 5~20분마다 부르는 코드)

| 파일 | 하는 일 |
|---|---|
| `analysis.py` | 분석 실행기: 무엇을 몇 분마다 돌릴지(`EVERY`) |
| `ledger.py` | 공개 장부를 이어 읽고 시장 요약을 만든다 |
| `scoreboard.py` | board 공식을 다시 맞추고 순위, 속도, 격차를 계산 |
| `objective.py` | buyer의 마진 몫 하한: 남은 시간 동안 saved와 거래 수의 득실 |
| `experiments.py` | 첫 호가와 흥정 방식의 A/B arm, 사람이 승자를 고정할 수 있음 |
| `shop_model.py` | 가게별 첫 호가, 양보, 성사율 |
| `linewatch.py` | 고가 라인 상한: 팔리면 +1, 안 팔리면 제외 |
| `watch.py` | 시장 규칙 변화 감시 |
| `journal.py`, `notes.py`, `forum.py` | buyer 이름으로 쓰는 시세 글과 데이터 노트, 투표 |

느린 루프 (meta-harness, 3시간마다)

| 파일 | 하는 일 |
|---|---|
| `context.py` | 다섯 부분 브리핑(상태, 인사이트, 보고서, 메모, 숫자), 부분마다 크기 한도 |
| `analysts.py` | seller와 buyer 분석(gpt-6.1-terra), 통합 분석(gpt-6.1-sol), 코드 검증, 채택과 되돌림 |
| `insights.py` | 통합 분석이 배운 것(발견, 근거, 쓰임), 실행마다 확인하거나 반박 |
| `data_room.py`, `mcp_host.py`, `tools.py` | MCP 데이터룸: 툴 4개(ledger_query, replay, shop_credit, leave_memo)와 자원 4개 |
| `check_models.py` | 서버에서 세 모델이 답하고 툴을 부르는지 한 번 확인 |

## 테스트

일곱 묶음, 약 680개 검사다. 배포 전마다 서버 복사본에서 돌렸고 결과는 `logs/*-test.log`에 있다.

```bash
KIT_FILE=../../../../scripts/market_kit.py PYTHONUTF8=1 python test_pipeline.py
```

나머지(`test_buyer.py`, `test_analysis.py`, `test_harness.py`, `test_decisions.py`, `test_data_room.py`,
`test_system.py`)도 같은 방법으로 돌린다. 모델과 시장은 가짜로 대신하므로 키와 네트워크가 필요 없다.

## deploy/

`DEPLOY.md`가 서버 설치 순서다. 서비스 파일 세 개, 설치(`install.sh`)와 점검(`check.sh`), 매일 seller
재시작(`restart_seller.sh`), 키를 화면에 보이지 않고 넣는 `secrets.sh`가 있다. 라운드 끝에 쓴 것은
`freeze_rules.py`(마지막 12시간 규칙 동결)와 `final_capture.py`(종료 board 저장)다. 날짜가 붙은 파일은
그날 한 번 돌린 기록 수리 스크립트이고, 무엇을 왜 고쳤는지 각 파일 첫머리에 있다. 서버에서 손으로 한 일은
모두 `../logs/manual_runs.log`에 남겼다.
