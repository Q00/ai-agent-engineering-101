# 키트 v4와 새 중간 평가 전환

새 기준은 저장소 `week-08.html` 및 https://market.ourolabs.ai/agent.md 이다. 키트 v4는 upstream/main 병합으로 받았으며 별도 포크 Sync 버튼과 같은 변경을 로컬에 반영했다. 원격 포크에는 아직 push하지 않았다.

## 진행 기록

- 기존 week-08 보고서와 계획은 `legacy-reproduction-report/`에 복사해 보존했다. 이제 운영 보고서 `REPORT.md`는 새 기준으로 작성한다.
- week-07 전체를 기존 week-08 파일을 덮어쓰지 않는 방식으로 복사했다. 로그 약 89만 파일을 포함하며 병렬 복사 종료 코드는 3(복사 완료, 기존 대상의 추가 파일 존재)이었다. 기존 원본을 삭제하거나 이동하지 않았다.
- shop_key.pem과 buyer_key.pem의 바이트 동일성, 복사된 Python 환경의 필수 모듈 import, Git 개인키 제외를 확인했다. 키 내용은 기록하지 않는다.
- 현재 작업 세션에는 MARKET_OWNER가 없어 재등록을 실행하지 않았다. 기존 터미널에서 week-07 키트를 Ctrl-C로 종료한 후 아래 명령으로 새 폴더를 사용한다. 클레임 코드는 터미널 환경변수로만 제공한다.

```powershell
& ./submissions/24522014/week-08/.venv/Scripts/python.exe -X utf8 -u ./submissions/24522014/week-08/run_market.py --tunnel
```

키트가 append하는 기존 market-kit.log에는 과거 버전도 남는다. 새 실행 구간의 첫 `[kit]` 줄에 `market_kit 4`가 찍히고 shop/buyer 등록이 뒤따르는지 확인한다. 과거 로그를 잘라내지 않는다.

## 다음 코드 전환의 계약

원본 계약은 `logs/agent-contract-20261102.md`다. 기존 seller와 buyer의 세 품목 제약은 새로운 SKU 상품을 처리하지 못한다. v4 연결 자체를 새 경제 기능 구현 완료로 보고하지 않는다.

1. seller: MARKET_DIR의 inventory.json에서 실제 unit을 고르고 SKU·원가·품질·인증서·유통기한을 반영한 서명 영수증으로 판매한다.
2. buyer: order.json의 acceptable SKU, 사용 기한과 예산, shops.json의 offers를 입력으로 사용한다. payments.jsonl의 실패 원인과 deal.json·broken.jsonl의 결과를 다음 선택 기억에 연결한다.
3. restock: 60초마다 실행하는 훅에 자금·재고·폐기 위험과 관측한 판매 수요를 반영한다. 기본 무작위 공급과 자체 정책을 구분한다.
4. review: 정산 후 deal.json을 근거로 평가하며 추정과 관측을 구분한다. 게시판은 outbox.jsonl을 통해 전송한다.
5. 구조 확정 후 architecture.tex/PDF, 운영 로그와 정산 실적을 보고서에 연결한다. 모델·메모리·툴의 작동 및 자체 구조를 설명하고, 실제 정산 20건씩과 Players 순위는 별도로 검증한다.
