# R3 로컬 준비 — 시장 미투입

사용자 지시: 기존 매매·VPS·7주차 제출은 그대로 두고, 기존 하네스에 ACP checkout/AP2 형태 정산 연결을 준비해 로컬 검증하고 옵시디언에 기록한다. 실제 시장 등록·주문·결제·배포·운영 한도 변경은 하지 않는다. 외부 모델 API도 이번 로컬 검증에서 호출하지 않는다.

기준: 교수 `upstream/main` c89c58d의 키트 v3. 아직 weeks/week-08/README.md·8주차 전용 검사기는 없다. 웹 강의의20건 기준에는 초안 주석이 있어 확정 요건으로 주장하지 않는다.

1. 7주차 최종 e75e769의 하네스·소스·테스트만 week-08에 이어받고 키·환경·운영 기억·과거 로그는 복사하지 않는다.
2. 공식 v3의 AcpShop·acp_pay를 재사용하는 로컬 gateway를 붙인다. `--deployed`는 ACP proxy를 자동으로 만들지 않으므로 직접 배포 구조의 앞단에서 acp.json과 checkout을 제공한다.
3. 임시 키와 로컬 정산 검증 서버로 실제 A2A receipt→checkout→closed mandates→정산을 검증한다. 외부 모델 응답은 오프라인 fixture로 대체하고 이 범위를 명시한다.
4. 정상 경로·서명/금액/상품/수취인/주문 재사용/만료/예산 초과 실패를 검증하고 원본 로그·결과·한계를 남긴다. 수업 키트가 보장하지 않는 범위는 별도로 기록한다.
5. 준비된 코드·로컬 검사 결과와 실제 R3 투입 전 남은 조건을 기존 과목 허브/current_state와8주차 노트에 연결한다. 원격 푸시·PR·실제 시장 투입은 하지 않는다.

최신 범위(사용자 후속 지시): VPS 배포 직전까지 설치·실행·패키징을 준비한다. 운영 구성에서는 공식 v3 키트의 `--public-url` proxy가 A2A와 ACP를 함께 담당한다. `--deployed` 경로를 위해 인증값을 별도로 추출하는 어댑터는 만들지 않는다. 로컬 `r3-seller` gateway는 독립 서비스형 연결을 검증하는 진단 경로로 남긴다. 기존 VPS 운영·주문·한도는 변경하지 않으며 새 구성은 명시적 활성화와 미래 종료시각·남은 주문 수 없이는 참가하지 않는다.

완료한 추가 단위: 중간2/판서 원문 수신·대조,100개 가게 노트 유지·SQLite WAL producer/별도 recorder·지연 측정, 외부 operator 피드백 증거 검증/프로필 버전 관리, 예산 이중 방어·동시 complete 검증. `REPORT.md`, `REQUIREMENTS.md`, `deploy/vps/README.md`가 현재 구현과 전환 절차의 기준이다. 실제 Docker 이미지 빌드·VPS 반영·운영 데이터 이전·R3 등록/거래는 다음 전환 단계이며 이번에 하지 않는다.


## 2026-10-31 Round 3 v4 전환 (실행)

사용자 지시로 전략 문서(Obsidian RAW `261031_AX중간평가_1등전략_Claude최종의견.md`)의 필수 1~7을 구현했다. 기준은 upstream d4b4d53(kit v4)이다.

1. v4 입력 호환(offers·acceptable), 키트 pin v4, 런처 훅 3종, 운영 구간 16일·주문 무제한.
2. 재고 판매자 `r3/seller.py`(정책표·정직 claim·A등급 보증·cert), fast buyer `r3/buyer.py`(정가×증거 가중치, 2~4턴, accept 정책), `r3/restock.py`, `r3/review.py`, `r3/forum.py`, 공개 원장 미러 `r3/ledger_db.py`, 정책 관리자 `r3/manager.py`.
3. 후기·사고 문장에서 비공개 예산 제거.
4. 검증: 전체 회귀 PASS, 원본 kit v4 Proxy/AcpShop/acp_pay 통합 검사, 공개 원장 읽기 전용 수집 1회.
5. 남은 것: VPS 이미지 재빌드·runtime.json·라운드2 join 확인(11/2), 개장 뒤 활성화, REPORT §5 결과 기록.
