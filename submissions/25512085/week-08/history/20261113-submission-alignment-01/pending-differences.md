# 운영 소스와 로컬 제출 소스 차이

운영 기준은 VM HEAD `6bb3cd8…`, 로컬 시작 기준은 `5513728…`이다. 로컬 변경은 이미 커밋되어 있어도 **VM 배포와 동의어가 아니다**. 이번에는 어느 쪽도 덮어쓰지 않았다.

원시 SHA-256 불일치와 Windows 줄바꿈 차이를 구분했다. 자세한 해시·변경 행 범위는 `source-comparison.json`. 아래는 줄바꿈 정규화 후 비교다.

| 파일 | 운영 | 로컬 pending / 보고 시 조치 |
|---|---|---|
| seller.py | 협상 생성 TTL>90s, committed>120s; 예약 in-memory | idle>90s/생성>=900s, committed300s; 파일 복구·원자적 저장·fsync 추가. **운영 반영 주장 금지**. 로컬 76–78,943–1003행 |
| store.py | 파일 재읽기, 기존 hub file lock | HubLockTimeout/skip decorator, mtime+size 기반 process catalog cache 등 추가. 운영에는 catalog snapshot cache 없음 |
| seller_cash_policy.py | 단일 588행, `_cash_*` import 없음 | facade 334행으로 바뀌고 `_cash_demand/_forum/_pricing/_risk`에 분리. import closure가 다르므로 facade만 운영에서 복사하거나 vice versa 하지 않음 |
| restock.py | 418행, 단일 cash policy의 계획·availability 사용 | 375행, 일부 계획·시장비율 함수 내용이 다름. 같다고 간주 금지 |
| market_buyer.py | 1548행 | 1560행; run_attempt/_run_attempt 내용 차이. 핵심 Saved 정책 존재와 전체 소스 일치는 별개 |
| review.py | 213행 | 218행; main 차이. 운영 근거 인용은 mirror 행 번호 사용 |

VM에 없는 로컬 파일 7개: `_cash_demand.py`, `_cash_forum.py`, `_cash_pricing.py`, `_cash_risk.py`, `seller_forum.py`, `seller_reputation.py`, `seller_market_research.py`. 이를 운영 아키텍처의 독립 실행 노드로 그리지 않는다.

정규화 후 같은 9개: `buyer.py`, `forum_writer.py`, `buyer_outcomes.py`, `adaptive_strategy.py`, `guarded_market_kit.py`, `visibility_guard.py`, `run_guarded_market.sh`, `seller_cash_policy.json`, `scripts/market_kit.py`.

예상 경로 `scripts/a2a.py`는 양쪽 모두 없었다. 운영 A2A는 `a2a` 패키지의 server/executor 인터페이스와 kit proxy다. 존재하지 않는 자체 a2a.py 소스를 제출 근거로 만들지 않았다.

## 배포 없는 변경과 미확인 기능 선긋기

- 로컬 테스트 통과·HEAD commit·원격 read-only 검증은 **배포·재시작 증거가 아니다**.
- runtime source mirror는 증거 전용이며 공식 제출 파일 교체가 아니다. 새 배포/소스 정렬은 별도 승인 필요.
- 실행 중 메모리의 정확한 모듈 해시는 읽지 않았다. 디스크 소스와 commit이 일치하고 역할별 process가 실행 중인 사실까지만 검증했다.
- 기존 REPORT의 300초 TTL 설명, 구현 시점의 모델 사용 설명, 예전 정산 업데이트 placeholder를 현 운영 사실로 그대로 재사용하지 않는다. 본 담당은 REPORT를 수정하지 않았다.
- 최신 제출용 전체 운영 원본 로그는 아직 확보·업데이트되지 않았다. 이번 excerpt를 `logs/market-kit.log`로 이름만 바꾸어 대체하면 안 된다.
