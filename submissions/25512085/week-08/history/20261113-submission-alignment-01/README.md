# 운영 정렬 evidence handoff

**새 증거만** 작성한 작업이다. 보고서·그림 담당은 먼저 `evidenceSummary.md`, 다음 `runtime-structure.md`, `pending-differences.md`를 읽으면 된다. 기존 REPORT/architecture/제출 소스/원본 logs/state는 수정하지 않았다. 다른 agent 호출 없음.

## 경로 주의

정확한 산출물 경로는 `submissions/25512085/week-08/history/20261113-submission-alignment-01/`이다. 최초 작성 시 담당 assistant가 경로를 잘못 해석했고, 사용자 정정에 따라 목적지 부재 및 전용 파일 33개의 tracked/무변경 상태를 확인한 뒤 `git mv`로 이 폴더만 이동했다. 수집 데이터·운영 mirror·기존 검증 기록의 내용은 보존했으며, 자체 경로 참조와 Python의 저장소 root 계산만 수정했다. 기존 제출·운영 파일은 변경하지 않았다. 이 evidence 폴더는 제출 필수 전체 원본 로그를 대체하지 않는다.

## 필수 산출물

- `runtime-manifest.json`: 운영/로컬 버전, 명시 allowlist 23개 경로, 존재15/부재8, source SHA 및 import 목록, 안전한 agent 명령, process 역할/시작시각.
- `runtime-source/`: 허용된 공개 소스15개만; raw runtime 데이터·agent.json 전체·credential은 없음. 내부 `.gitattributes`는 mirror의 원래 byte 보존을 위해 text 변환을 끈다.
- `runtime-final-readonly-verification.json`: VM tracked dirty0, 허용 소스 HEAD 및 최초 hash 대조.
- `sanitizedmetrics.json`: board GET **1회**의 자기 지표, 2개 로그의 각4 MiB tail 집계.
- `source-comparison.json`, `verification.json`: 줄바꿈 정규화 차이, 원본 보존, AST 구문·집계 검증.
- `final-validation.json`: 저장 snapshot만으로 수행한 18개 Python AST·privacy·원본 보존·수치 불변식 최종 검사.
- `logs/read-only-execution-summary.log`: 민감정보를 제외한 실행 요약. 전체 raw console transcript는 아님.
- `logs/settlement-substrings-excerpt.txt`: 실제 같은 줄의 안전한 부분 문자열만. **완전한 원문 줄/전체 원본 로그가 아님.**
- `evidenceSummary.md`, `runtime-structure.md`, `pending-differences.md`: 검증 사실·그림 노드/연결·미배포 차이.

## 실제 수행 명령

저장소 root의 기존 Python에서 각 수집·검증 명령을 최초 경로에서 한 번씩 실행했다. 아래는 이동 후의 동일 스크립트 경로이며, 경로 정정 작업에서는 live 수집을 재실행하지 않았다.

```powershell
python -B submissions/25512085/week-08/history/20261113-submission-alignment-01/collect.py source
python -B submissions/25512085/week-08/history/20261113-submission-alignment-01/collect.py metrics
python -B submissions/25512085/week-08/history/20261113-submission-alignment-01/verify_evidence.py
```

모든 SSH는 기존 gcloud compute ssh, project/zone/VM을 명시하며 Python `-B -c`로 읽기 전용 probe를 실행했다. mutation helper는 import하지 않았다. app import, API POST, 원장 대량조회, log 전체 전송 없음.

**위 명령은 live 재조회 명령이다. 동일 폴더 재실행 금지**: writer는 `xb`로 기존 evidence 덮어쓰기를 거부하지만 SSH 조회는 파일 충돌 검사보다 앞선다. 재관측은 별도 허용된 새 경로가 필요하다. 기존 snapshot 재검증만 하려면 `python -B .../validate_saved.py`를 사용한다. 이 명령은 네트워크를 사용하지 않는다.

이동 후 오프라인 재검증 명령:

```powershell
python -B submissions/25512085/week-08/history/20261113-submission-alignment-01/validate_saved.py
```

## commit 단위

1. `7d0eba0`: read-only source probe/transport 구현.
2. `e2afb33`: 운영 소스·board·bounded 로그 집계·검증 결과와 수집 코드.
3. 후속 문서/저장 snapshot 검사 commit: 안전한 handoff 및 byte-preserving attributes. commit을 squash/rewrite하지 않음. push하지 않음.
4. `dc9e5f1`: 규칙 기반 결정과 무작위 후기 템플릿 구분.
5. 경로 정정 commit: 전용 산출물만 `git mv`, 자체 경로·root 계산 수정, 오프라인 `validate_saved.py` 재검증. 기존 로그와 검증 JSON은 과거 기록으로 보존.

## 남은 작업 / 미실행

- 보고서·그림 담당이 운영 사실만 반영할 것. runtime monolith와 in-memory 예약, 비LLM 규칙 기반 Round3 가격·accept 경로를 기준으로 할 것. no-deal 후기에는 무작위 템플릿 선택이 있어 전체 출력을 완전 결정론적이라고 쓰지는 말 것.
- **제출용 최신 전체 운영 원본 로그는 미확보**. 기존 로컬 원본은11월5일이며 원산지 미확정. 별도 명시 수집·민감정보 검토 필요.
- 공식 check_week08 스크립트는 이 checkout에 없어서 미실행. 결제·LLM·애플리케이션 회귀 테스트·deploy·restart는 이 범위에서 미실행.
- 지표 관측은11월13일 KST 중간 snapshot이며 종료시각 최종 점수가 아니다. 부분 로그의 broad refusal 문구를 전체 실패율·고유 주문 실패 수로 변환하지 말 것.
