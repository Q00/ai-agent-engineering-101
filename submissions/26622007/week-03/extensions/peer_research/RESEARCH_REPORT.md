# 폐쇄망 RAG 벡터 저장소 검토 — ADR-RAG-7429

합성 과제의 웹 조사 산출물을 Codex가 공식 문서와 대조해 정리한 검토본이다. 검토일: 2026-09-17.
원본: [Worker 통합 보고서](runs/20260917T030409-02-revise-0372e5/report.md), [구조화 산출물](runs/20260917T030409-02-revise-0372e5/artifacts/rag-revise.json).

## 결정과 전제

**pgvector를 우선 검증 후보로 유지한다.** 기존 PostgreSQL 운영 체계를 활용할 수 있다는 운영상의 판단이다.
성능 우위를 입증한 결정은 아니며, 검색 품질과 복구 리허설 결과에 따라 재검토한다.

|항목|확인한 조건|
|---|---|
|내부 결정 ID|ADR-RAG-7429|
|기존 PostgreSQL 백업 책임|플랫폼팀|
|규모|100만 개, 1536차원 벡터, 30개 tenant|
|검색과 변경|tenant 필터를 적용한 top-10, 하루 약 1만 벡터 갱신|
|인력|PostgreSQL 운영 인력 2명, 추가 인력 확보 불가|
|새 배포 조건|폐쇄망, 외부 관리형 SaaS 이용 불가|
|실측 상태|검색 성능·복구 시간 모두 `not_measured`|

결정 ID·담당 팀·규모는 이번 질문에 반복해서 넣지 않았다. 이전 실행의 메모리에서 회상했다.
이번 웹 조사는 설계 검토 환경에서 이루어졌다. 배포할 RAG 서비스의 외부 호출을 허용한다는 뜻은 아니다.

## 두 대안 비교

|기준|pgvector|자체 호스팅 Qdrant|
|---|---|---|
|기존 환경 활용|PostgreSQL 확장으로 기존 DB 운영 체계를 활용|별도 저장소의 설치·배포·백업 관리 필요|
|tenant 검색|근사 인덱스 스캔 후 필터 때문에 결과 수·recall을 확인해야 함|payload 기반 tenant 분리와 인덱스·sharding 전략 검토 가능|
|검색 품질 개선 후보|필터 컬럼 인덱스, iterative scan, tenant별 파티셔닝을 비교|tenant payload index 및 데이터 분포에 맞는 분리 방식 비교|
|복구 방식|base backup과 WAL 보관을 결합한 PITR 검토|컬렉션·노드 스냅샷 생성과 복구 절차 검토|
|운영 판단|기존 담당 조직·도구를 재사용할 여지가 큼. 추가 부담이 0이라는 뜻은 아님|별도 운영 절차와 책임 주체를 정해야 하므로 현재 인력 조건에서 부담이 큼|

pgvector 공식 README는 근사 인덱스의 필터 적용 순서, iterative scan과 tenant 격리를 위한 파티셔닝 등을 설명한다.
따라서 단순히 HNSW를 추가하는 것으로 검색 품질을 보장할 수 없으며 실제 tenant 분포에서 비교해야 한다. [pgvector 문서](https://github.com/pgvector/pgvector#filtering)

Qdrant의 공식 다중 tenant 가이드는 payload 분리, 사용자 지정 sharding, tiered multitenancy를 제시한다.
`is_tenant`는 tenant 필드의 인덱스 설정이며 사용자 권한 검사 자체를 대신한다고 해석하면 안 된다.
인증·권한 경계와 데이터 필터를 함께 검토하고 교차 tenant 누출을 별도로 검사한다. [Qdrant 다중 tenant 문서](https://qdrant.tech/documentation/manage-data/multitenancy/)

PostgreSQL PITR은 적절한 base backup과 연속된 WAL 보관이 전제다. 논리 덤프만으로 대체할 수 없고,
이 방식의 복원 범위는 전체 클러스터다. [PostgreSQL PITR 문서](https://www.postgresql.org/docs/current/continuous-archiving.html)

Qdrant 스냅샷에는 특정 노드의 컬렉션 데이터와 설정이 담긴다. 분산 구성은 노드별 생성과 복구 조건을 확인해야 한다.
현재 문서는 같은 minor 또는 다음 minor 버전으로의 복원을 설명하며 같은 minor 안에서도 patch 호환 조건을 예제로 제시한다.
시작 시 복구 방식은 단일 노드에 한정된다. 이 근거만으로 Qdrant의 모든 배포 방식에 대해 PITR 가능 여부를 단정하지 않는다.
[Qdrant 스냅샷 문서](https://qdrant.tech/documentation/operations/snapshots/)

## 다음 검증 항목

- [ ] exact 검색 결과를 기준으로 tenant별 recall@10, 반환 건수 부족, p50/p95/p99 지연을 측정한다.
- [ ] tenant 분포의 편향, 필터 선택도, 동시 요청, 하루 1% 갱신을 반영한다.
- [ ] 필터 누락·잘못된 tenant ID·권한 없는 요청에서 데이터 누출이 없는지 확인한다.
- [ ] pgvector의 파티셔닝·iterative scan·인덱스 설정별 비용을 비교한다.
- [ ] PostgreSQL PITR과 Qdrant 스냅샷 복구를 실제로 수행하고 RTO/RPO, 필요한 디스크 공간, 복구 후 품질을 측정한다.
- [ ] 폐쇄망 반입 패키지·이미지·모델·업데이트 경로를 확인한다.
- [ ] Qdrant를 채택한다면 별도 백업 담당 조직과 보관·복구 절차를 먼저 정한다.

Worker가 제시한 recall@10 0.95, p95 100ms, 동시성 8은 **검토할 목표 후보**다.
사용자가 확정한 요구사항이나 측정 결과가 아니므로 수용 기준으로 확정하기 전에 검토해야 한다.
비용·지연·복구 시간의 실측값은 아직 없다.

## 원본 검토에서 수정한 점

원본의 오탈자와 팀 이름 축약을 바로잡고, 운영 부담이 정량적으로 확인되었다거나 추가 부담이 없다는 단정을 제거했다.
Qdrant PITR에 대한 포괄적 부정도 문서에서 확인한 스냅샷 방식의 설명으로 제한했다.
자동 검사 통과는 메모리 회상·출처 조회·데이터 계약의 증거이며 보고서 전체 내용의 정확성 보증은 아니다.
원본 보고서·JSON·실행 로그·장기 메모리는 이 검토본 때문에 수정하지 않았다.
