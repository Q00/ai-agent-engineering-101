# Week 07 myeonggi

구매자는 예산 안에서 가능한 정상 구매를 성사시키고 판매자는 하한가를 지키면서 가성비와 체결률을 우선함
이름은 Agent Card와 시장 등록 닉네임 모두 `myeonggi` 사용함

저장소 루트에서 실행함

```bash
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"
python -m pip install -r submissions/26510358/week-07/requirements.txt
python scripts/market_test_week07.py submissions/26510358/week-07
python scripts/check_week07.py submissions/26510358/week-07
```

시장 재참가는 `/join`에서 받은 본인 claim code를 `MARKET_OWNER` 환경변수에 설정한 뒤 실행함
이미 실행 중인 키트와 중복 실행하지 않음

```bash
python scripts/market_kit.py join \
  --dir submissions/26510358/week-07 \
  --market https://market.ourolabs.ai \
  --name myeonggi \
  --tunnel
```

실시간 로그 확인

```bash
tail -n 100 -F submissions/26510358/week-07/logs/market-kit.log
```

- `seller.py`는 실제 모델이 가격과 판매 여부를 정하고 코드는 하한가와 정수 가격을 검사함
- `buyer.py`는 실제 모델이 구매를 결정하고 코드는 예산·품목·합의 가격·task ID·중복 구매를 검사함
- `market_buyer.py`는 리뷰와 `.market/buyer-memory.json`을 읽어 상점을 고름
- `receipts.py`는 `{shop,item,price,task}`를 정규화해 Ed25519 서명함
- `inference_pool.py`는 기본 판매자 2개·구매자 1개 추론 슬롯을 사용해 판매 요청 폭주가 구매자를 막지 않게 함
- 명확한 과제 프로토콜 가격 문장은 추가 추론 없이 읽고 모호한 문장은 실제 관찰 하네스로 확인함
- 리뷰 조작 지시와 모순된 영수증은 기억하고 회피함
- 판매자 서명의 등록 키 검증과 구매자 서명 추가는 공식 시장 키트가 담당함
- 정책은 현재 체결 우선 고정 기준임 이전 합성 bandit 정책은 `policies/week06-synthetic-reference.json`에 보존했으며 실거래로 학습한 RL이라고 주장하지 않음

시장 라운드는 운영자가 정함 Round 1에는 리뷰·기억을 사용하며 Round 2에는 같은 코드에 영수증 서명 검증이 적용됨
개인키 `*.pem`, 실행 상태 `.market/`, 대량의 호출별 상세 로그 `logs/runtime/`는 Git에서 제외함
공식 검증 로그와 과제 증거는 `logs/`에 보존함

판매자·공통 코드·`agent.json`을 바꾸면 키트를 재시작함
같은 닉네임과 키를 사용해야 이후 거래가 같은 이력에 쌓임
`SUNGMYEONGGI`, `gacha-shop`의 이전 거래는 기존 이름에 남고 `myeonggi` 이력은 새로 집계됨
현재 임시 공개 주소와 로컬 PID는 `.market/session.json`에서 확인함
