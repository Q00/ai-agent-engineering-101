# 웹 조사 산출물

실행: `20260917T024752-02-revise-no-memory-6fd3ef`

상태: succeeded / 자동 계약 검사: False

## 변경 ADR: RAG 벡터 저장소 선정 — 폐쇄망 전환 반영 (rag-vector-adoption 후속)

### 1. 이전 대비 변경점
- **배포 환경이 폐쇄망으로 전환**되어 외부 관리형 SaaS를 사용할 수 없다 → `external_saas_allowed=false`. 두 후보(pgvector, 자체 호스팅 Qdrant) 모두 셀프호스팅 전제로만 비교한다.
- **PostgreSQL은 이미 운영 중**이고 **신규 stateful 저장소 운영 인력 증원 불가** → 운영 부담이 낮은 쪽에 가중치를 둔다.
- 이전 실행의 장기 메모리(personal_memory/handoff_memory)가 **비어 있어** 내부 결정 ID·PostgreSQL 백업 담당 팀·기존 규모·품질 목표를 확인할 수 없다 → 추측하지 않고 `unknown`/확인 필요로 반환한다.
- 이전 추천을 무조건 유지하지 않고, 새 제약(폐쇄망·인력 고정)을 우선해 재평가했다.

### 2. 결정 동인
| 동인 | 내용 | 영향 |
|---|---|---|
| 폐쇄망 | 외부 SaaS·외부 호출 불가 | 자체 호스팅만 허용 |
| 기존 PostgreSQL 운영 | 표준 백업/PITR 체계 보유 | pgvector 운영 부담 최소 |
| 인력 증원 불가 | 신규 stateful 서비스 운영 불가 | Qdrant 도입 시 리스크 |
| 테넌트 격리·품질 | 누출률 0, recall@k 목표 | 검증 게이트 필요 |

### 3. 비교 표
| 항목 | pgvector (PostgreSQL 확장) | 자체 호스팅 Qdrant |
|---|---|---|
| 폐쇄망 적합성 | 기존 PostgreSQL 확장 설치, 신규 포트·인증체계 불필요 | 별도 서비스·컨테이너·포트·스토리지 운영 필요 |
| 백업 | `pg_dump`(-Fc/-Fd 선택적·병렬 복원), `pg_basebackup`(클러스터 전체, PITR) | Qdrant 스냅샷 API/스토리지 스냅샷 의존 |
| 복구 | 기존 DBA 런북 재사용, `CREATE EXTENSION vector` 및 벡터 인덱스 재생성 필요 가능 | 스냅샷 복원 후 컬렉션 재로드·재색인, 신규 런북 필요 |
| tenant 필터 | `tenant_id` 컬럼 + B-tree/partial index + RLS로 DB 레벨 강제 | payload index 기반 필터, 컬렉션 분리로 물리 격리 |
| 운영 인력 | 기존 체계 편입, 추가 인력 불필요 | 신규 stateful 운영 인력 필요(증원 불가와 충돌) |
| 품질 리스크 | 필터 선택도 낮을 때 recall 변동 → partial index·ef_search 튜닝 필요 | payload 사전 필터로 격리 명확 |

### 4. 최종 판단
폐쇄망 + 기존 PostgreSQL 운영 + 인력 증원 불가 제약에서 **pgvector를 기본 채택**한다(`recommended_storage=pgvector`). 근거: (1) 백업/복구가 PostgreSQL 표준 도구로 확립되어 있고, (2) 신규 stateful 저장소 운영 부담이 없으며, (3) RLS로 테넌트 격리를 DB 레벨에서 강제할 수 있다. 단, 골든셋에서 테넌트 누출률>0 또는 recall@k 임계 미달 시 **자체 호스팅 Qdrant를 대안**으로 재검토한다.

### 5. 배포/복구 체크리스트
- [ ] 폐쇄망 내 PostgreSQL 확장 패키지 반입 경로·버전 고정 확인
- [ ] `pg_dump -Fc`/`-Fd` 정기 백업 + `pg_basebackup` 기반 PITR 정책 문서화
- [ ] 복구 리허설 시 `CREATE EXTENSION vector` 및 벡터 인덱스 재생성 절차 검증(미실행)
- [ ] tenant 필터 정확성·검색 품질(recall@k, nDCG, MRR, 누출률 0, p95) 검증 계획 수립(미실행)
- [ ] Qdrant 대안 검토 시 스냅샷/복원 공식 문서 재확보

### 6. 재검토 조건
- 테넌트 누출률>0 또는 recall@k 임계 미달, p95 지연 목표 초과 시 Qdrant 재평가
- 폐쇄망 반입 절차 변경 또는 운영 인력 확보 시 재평가

### 7. 미해결 항목
- 내부 결정 ID, 백업 담당 팀, 기존 규모·품질 목표: 메모리 부재로 unknown
- 실제 벤치마크·복구 리허설 미수행 → `measured_latency=not_measured`
- Qdrant 스냅샷/복원 세부 절차 재확인 필요

> 실제 성능 측정·복구 테스트는 수행하지 않았으며 성공을 주장하지 않는다.

## 기록된 사실

- **recommended_storage**: pgvector
- **internal_decision_id**: unknown
- **backup_owner**: unknown
- **measured_latency**: not_measured
- **unresolved**: 내부 결정 ID·백업 담당 팀·기존 규모/품질 목표 메모리 부재로 unknown; 실제 벤치마크·복구 리허설 미실행; Qdrant 스냅샷/복원 세부 절차 재확인 필요
- **memory_refs**: none
- **external_saas_allowed**: False
- **pgvector_backup_tool_pg_dump**: supported
- **pgvector_backup_tool_pg_basebackup**: supported
- **pgvector_pitr_supported**: True
- **qdrant_self_hosted_required**: True
- **new_stateful_store_ops_headcount_available**: False
- **benchmark_executed**: False
- **recovery_test_executed**: False
- **tenant_filter_validation_executed**: False
- **quality_measured**: False
- **revisit_condition**: tenant 누출률>0 또는 recall@k 임계 미달 또는 p95 목표 초과 시 Qdrant 재평가

## 근거

- https://www.postgresql.org/docs/18/app-pgdump.html — pg_dump custom(-Fc)·directory(-Fd) 아카이브로 선택적·병렬 복원, 아키텍처 간 이식 가능 → 폐쇄망 백업/부분 복구 근거
- https://www.postgresql.org/docs/18/backup-dump.html — 텍스트 덤프는 psql, 비텍스트는 pg_restore, 복원 전 template0 기반 DB 생성 필요 → 기존 DBA 런북 재사용 근거
- https://www.postgresql.org/docs/current/app-pgbasebackup.html — pg_basebackup은 실행 중 클러스터 전체 물리 백업, PITR·복제 스탠바이 시작점, 개별 DB 백업 불가 → 클러스터 단위 보호 근거
- https://www.postgresql.org/docs/current/continuous-archiving.html — 물리 백업·PITR 복구 체계 근거
- https://www.postgresql.org/docs/current/ddl-rowsecurity.html — RLS로 테넌트 격리를 DB 레벨 강제 근거
- https://github.com/pgvector/pgvector — HNSW/IVFFlat 인덱스, 필터링 검색, 오프라인 빌드 가능 근거
- https://github.com/pgvector/pgvector#filtering — WHERE 조건과 벡터 검색 결합, partial index 활용 근거
- https://qdrant.tech/documentation/concepts/filtering/ — payload 기반 필터, is_empty/is_null 조건 근거
- https://qdrant.tech/documentation/guides/multiple-partitions/ — tenant_id payload index 및 멀티테넌시 권장 패턴 근거
- https://qdrant.tech/documentation/concepts/snapshots/ — 스냅샷 기반 백업·복구 근거
- memory:none — personal_memory/handoff_memory가 비어 있어 내부 결정 ID·백업 담당 팀·기존 규모/품질 목표는 unknown으로 처리(추측 금지)
- 측정 미실행: recall@k/nDCG/MRR/p95 및 복구 리허설은 실제 수행하지 않음 → not_measured/검증 필요
