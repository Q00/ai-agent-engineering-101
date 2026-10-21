# Cloudflare Workers 가게

기존 seller_agent.py의 규칙 기반 가게를 Workers JavaScript로 옮겼다.
가게만 이전한다. buyer와 market_kit.py는 여전히 별도 컴퓨터에서 실행해야 한다.

## 구조
- `src.js`: A2A v1.0 JSON-RPC의 SendMessage, GetTask, CancelTask와 Agent Card.
- SQLite Durable Object 하나가 호가, 방문 횟수, 흥정 상태, 완료 영수증을 보관한다.
- 모든 흥정 변경을 직렬화하여 동시에 온 요청이 같은 거래를 중복 완료하지 않게 한다.
- 첫 실행은 wrangler.jsonc의 INITIAL_STATE로 시작한다. 이후에는 저장된 상태를 쓴다.
- 개인키는 SHOP_PRIVATE_KEY secret으로만 전달한다. 공개 주소나 소스에 넣지 않는다.
- 스트리밍은 지원하지 않으며 카드에도 streaming:false로 표시한다.
- 기존 가격·초기 30회·후기 할인 규칙을 옮겼다. 방문은 고유 고객이 아니라 새 흥정 횟수다.
- 이름/키를 유지하여 시장에 재등록한다. 과거 후기·광고 전략은 기존 실험 기록에 남아 있다.

## 무료 배포
Wrangler 로그인과 Cloudflare 이메일 인증이 필요하다. 유료 플랜 전환은 하지 않는다.
무료 요청/CPU/저장소 할당량을 초과하면 서비스가 제한될 수 있다.

이 폴더에서:

```bash
npm ci
npx wrangler login
npx wrangler deploy
npx wrangler secret put SHOP_PRIVATE_KEY < ../shop_key.pem
```

배포가 성공하고 비밀키를 넣기 전에는 거래를 받지 않는다.
기존 실제 shop_key.pem을 사용해야 시장의 공개키와 일치한다.

## 확인
로컬 시험은 별도로 생성한 시험용 키를 `.dev.vars`에 두고 `npm run dev`로 실행한다.
`.dev.vars`와 `.wrangler`는 커밋하지 않는다.

레포 루트에서:

```bash
.venv/bin/python submissions/26520057/week-07/workers/verify.py http://localhost:8988
.venv/bin/python submissions/26520057/week-07/workers/verify.py https://<배포주소> --production
```

실제 서버 검증은 가게의 방문/가격 상태를 변경하지만 시장 주문이나 후기를 만들지 않는다.
수업 전체 market test를 대체하지 않는다.

## 시장 연결
공개 주소에서 영수증 검증까지 성공한 뒤 기존 키트를 멈추고 같은 이름/키로 실행한다.
MARKET_OWNER는 로컬 환경변수로 설정한다. 키와 claim code를 커밋하지 않는다.

```bash
source .venv/bin/activate
python scripts/market_kit.py join --dir submissions/26520057/week-07 \
  --market https://market.ourolabs.ai --name cheapest-in-town --deployed https://<배포주소>
```

Workers로 가게가 옮겨져도 위 키트와 buyer는 아직 노트북에 의존한다.
Workers 영수증·흥정 상태는 재배포 후에도 남지만 예전 Python 가게의 진행 중 흥정은 이관하지 않는다.

## 2026-10-21 진행 기록
- 로컬 세 품목: 하한가, 서명, 변조 거부, 완료 task 재사용 거부, GetTask 및 취소 통과.
- 첫 배포 시도: Cloudflare 오류 10034, 이메일 인증 필요. 배포 성공 전 상태.

## 이메일 인증 후 배포 결과
- 공개 주소: https://cheapest-in-town-26520057.cheapest-in-town-worker.workers.dev
- 기존 가게 개인키를 Cloudflare secret으로 설치했다. 개인키 내용은 로그/소스에 남기지 않았다.
- 실제 공개 주소에서 세 품목의 하한가, 서명, 변조 거부, 완료 후 메시지 거부, GetTask, 취소를 확인했다.
- 공개 주소 검증에 새 흥정 4건을 사용했다. 이들은 시장의 거래·후기로 제출하지 않았다.
- `agent.json`의 deployed와 후기 감시 기본 주소를 위 주소로 변경했다.
- buyer/키트는 Mac에서 --deployed로 계속 실행한다. 가게만 Cloudflare로 이전했다.
