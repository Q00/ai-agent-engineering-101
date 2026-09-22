# 웹 조사 산출물

실행: `20260917T030157-02-revise-fd93ee`

상태: succeeded / 자동 계약 검사: False

## 폐쇄망 전환 후속 검토: pgvector vs 자체 호스팅 Qdrant (ADR-RAG-7429)

### 1. 이전 대비 변경점

| 구분 | 이전 실행 | 이번 실행(폐쇄망) |
|---|---|---|
| 배포 환경 | 외부 관리형 SaaS 검토 가능 | **폐쇄망, external_saas_allowed=false** |
| 인력 | PostgreSQL 운영 2명 | 2명 유지, **stateful 저장소 증원 불가** |
| Qdrant 근거 | 공식 URL 미확보 | 스냅샷·설치 공식 문서 확보 |
| 권고 | pgvector 1순위(근거 일부 미확보) | **pgvector 1순위 유지, 근거 강화** |

### 2. 비교 표 (폐쇄망 기준)

| 항목 | pgvector (PostgreSQL 확장) | 자체 호스팅 Qdrant |
|---|---|---|
| 폐쇄망 설치 | 확장 오프라인 반입 후 `CREATE EXTENSION`, 별도 프로세스 없음 | 컨테이너/바이너리 오프라인 반입, 별도 stateful 서비스 기동 |
| 백업 | 파일시스템 백업+WAL 아카이빙(PITR), `pg_dump` 논리 덤프 보조 | 스냅샷 API(컬렉션/전체) |
| PITR | 지원(전체 클러스터 단위, 부분집합 복원 불가) | 시점 복구 미지원, 스냅샷 시점 복원만 |
| 복구 범위 | 클러스터 전체 복원 | 컬렉션/스냅샷 단위 복원 가능 |
| tenant 필터 | 근사 인덱스 post-filter, 0.8.0+ iterative scan, LIST 파티셔닝 권장 | payload 필터를 HNSW 탐색에 통합, payload 인덱스 |
| 운영 주체 | 기존 플랫폼팀 체계 재사용 | 신규 백업 파이프라인·복구 훈련 별도 수립 |
| 인력 부담(2명) | 추가 도구 없음, 기존 런북 확장 | 신규 stateful 저장소 1개 추가 운영 |

### 3. 최종 판단

**pgvector를 1순위로 유지한다.** 근거: (1) 폐쇄망에서 기존 PostgreSQL 백업 체계(PITR+pg_dump)를 재사용해 인력 2명 부담이 최소이고, (2) Qdrant는 스냅샷 공식 근거는 확보했으나 PITR 미지원·신규 stateful 운영 부담이 증원 불가 제약과 충돌한다. pgvector의 post-filter 약점은 tenant LIST 파티셔닝+iterative scan으로 완화하되 **실측 검증 전까지 확정이 아니다.**

### 4. 배포/복구 체크리스트

- [ ] pgvector 확장 오프라인 반입·`CREATE EXTENSION` 검증
- [ ] tenant_id LIST 파티셔닝 + 파티션별 HNSW 인덱스 생성
- [ ] iterative scan(`hnsw.max_scan_tuples`) 설정 및 필터 선택도별 recall 측정
- [ ] WAL 아카이빙+PITR 복구 리허설(플랫폼팀)
- [ ] 파티션별 인덱스 재구축 주기 확정
- [ ] Qdrant 대안 스냅샷 복구 절차 문서화(2순위)

### 5. 미해결 항목

실제 latency·recall·복구 시간 미측정(not_measured), Qdrant 스냅샷 복구 시간·폐쇄망 오프라인 설치 세부 절차 미확보, pgvector 파티션 인덱스 재구축 주기 미검증. 최종 저장소는 실측 검증 전 확정 보류.

## 기록된 사실

- **recommended_storage**: pgvector(기존 PostgreSQL 백업 체계 재사용, 폐쇄망 적합) 1순위, 자체 호스팅 Qdrant 2순위(신규 stateful 운영 부담)
- **internal_decision_id**: ADR-RAG-7429
- **backup_owner**: 플랫폼팀
- **measured_latency**: not_measured
- **external_saas_allowed**: False
- **memory_refs**: mem-4a1df38327a7d1de,mem-65823fff470a3d74,mem-228e3524f75d90b1,mem-32d20f79baea194f
- **unresolved**: 실제 latency·recall·복구 시간 미측정; Qdrant 스냅샷 복구 시간·폐쇄망 오프라인 설치 세부 절차 미확보; pgvector 파티션별 인덱스 재구축 주기 미검증; 최종 저장소는 실측 검증 전 확정 보류
- **vector_count**: 1000000
- **dimension**: 1536
- **tenant_count**: 30
- **daily_update_vectors**: 10000
- **ops_headcount**: 2
- **pgvector_pitr_supported**: True
- **pgvector_pitr_scope**: 전체 클러스터 단위(부분집합 복원 불가)
- **pg_dump_is_logical_not_pitr**: True
- **qdrant_snapshot_supported**: True
- **qdrant_pitr_supported**: False
- **qdrant_offline_install_required**: True
- **pgvector_filter_mode**: post-filter (approximate index), iterative index scan since 0.8.0
- **pgvector_tenant_strategy**: LIST partition by tenant_id + per-partition HNSW
- **qdrant_tenant_strategy**: per-tenant collection or single collection with tenant_id payload index
- **benchmark_executed**: False
- **cost_figures_fabricated**: False
- **recommendation_changed**: False

## 근거

- memory:mem-4a1df38327a7d1de — 규모 가정(100만×1536, 30 tenant, 일 1만 갱신), 인력 2명, ADR-RAG-7429, backup_owner=플랫폼팀, pgvector HNSW 기본값(m=16, ef_construction=64, ef_search=40), IVFFlat lists=100
- memory:mem-65823fff470a3d74 — pgvector PITR 지원, pg_dump는 논리 덤프로 PITR 대체 불가, PITR 복구 범위=전체 클러스터 단위, Qdrant 스냅샷 공식 근거 확보
- memory:mem-228e3524f75d90b1 — 통합 결정 문서, recommended_storage=pgvector 1순위/Qdrant 2순위, measured_latency=not_measured
- memory:mem-32d20f79baea194f — pgvector 인덱스·필터·tenant 분리 조사, Qdrant 공식 URL 미확보
- https://www.postgresql.org/docs/current/continuous-archiving.html — PostgreSQL 연속 아카이빙 및 PITR: 파일시스템 백업+WAL 아카이빙으로 특정 시점 복구, 전체 클러스터 단위
- https://www.postgresql.org/docs/current/backup.html — PostgreSQL 백업/복구 개요: SQL 덤프와 파일시스템 수준 백업의 구분
- https://www.postgresql.org/docs/18/app-pgrestore.html — pg_restore: 논리 아카이브 복원, PITR 대체 불가
- https://qdrant.tech/documentation/concepts/snapshots/ — Qdrant 스냅샷 개념: 컬렉션/전체 스냅샷 생성·복구, 자체 호스팅 스냅샷 API 근거
- https://qdrant.tech/documentation/guides/installation/ — Qdrant 자체 호스팅 설치(컨테이너/바이너리) 근거, 폐쇄망 오프라인 반입 필요성
- https://github.com/pgvector/pgvector — pgvector README: HNSW/IVFFlat 파라미터 기본값, post-filter 동작, iterative index scan(0.8.0+), LIST 파티셔닝 권장
- https://github.com/pgvector/pgvector/blob/master/src/hnsw.h — HNSW 기본 파라미터 상수(m=16, ef_construction=64) 근거
- https://github.com/pgvector/pgvector/blob/master/src/ivfflat.h — IVFFlat 기본 파라미터(lists=100, probes=1) 근거
- 실제 latency/recall 벤치마크 및 복구 리허설: 미실행(not_measured) — 본 문서는 문서 기반 설계 검토이며 성능·복구 성공을 주장하지 않음
