# LAB 1 Step 1: 가게 목록 읽기

2026-10-20, 학번 24522014 (kcwarni).

`shop_catalog.py`의 `load_shops(path)`가 UTF-8 JSON 파일을 읽어 가게 목록을 반환한다. 가게마다 `name`, `url`, `reviews`를 확인하고, 가게와 후기 순서는 그대로 유지한다. 이 함수는 시장 API를 호출하지 않고 키트가 넘겨주는 파일만 읽는다.

- `name`: 가게 식별자. 다음 단계에서 기억을 저장할 때 사용한다.
- `url`: 해당 가게의 A2A card URL. 키트를 다시 실행하면 바뀔 수 있다.
- `reviews`: 최신 후기가 앞에 오는 목록. 후기마다 `stars`, `review`, `round`가 있다. 빈 목록도 허용한다.

Round 1 이상에서 `agent.json`에 `market_buyer`가 있을 때 공식 키트는 주문마다 `.market/shops.json`을 만들고 그 경로를 `{shops}`에 넣어 실행한다. 키트는 내 가게를 목록에서 제외한다. 현재는 Round 0이고 아직 market_buyer를 연결하지 않았으므로 이 파일이 없다.

읽기 확인에는 두 자료를 사용했다.

1. `examples/shops-step1.json`: 강의의 maple-home/willow-goods 예시. 실제 거래 기록이 아니며, 예시 URL에 접속하지 않는다. 후기가 있는 가게와 없는 가게를 확인한다.
2. `logs/lab1-step1/shops-live.json`: 공개 시장 `/api/shops`에서 읽은 스냅샷. 내 가게 이름을 제외하고 키트의 입력 필드 3개만 저장했다. 키트가 주문에 대해 생성한 파일과 구분하기 위해 `.market/shops.json`에는 쓰지 않았다. 수집 시각과 Round는 `fetch.log`에 기록했다.

저장소 루트에서 실행:

```powershell
& ./submissions/24522014/week-07/.venv/Scripts/python.exe -X utf8 ./submissions/24522014/week-07/shop_catalog.py --shops ./submissions/24522014/week-07/examples/shops-step1.json
```

가게 선택, 후기 판단, 거래 기억, 실제 흥정은 Step 2에서 구현할 부분이다. 이 단계에서는 agent.json과 실행 중인 시장 키트를 변경하지 않았다.
