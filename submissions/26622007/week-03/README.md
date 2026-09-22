# Week 03 — 계획 심사와 재귀·병렬 위임

[제출 보고서](REPORT.md) · [결과 CSV](results.csv) · [재귀·상태 다이어그램](diagrams/CURRENT_STATES.md)

A/B/C가 계획·근거·확신도를 제안하고 요청자가 계획을 심사한다. 코드는 고정 규칙으로 낙찰자를 선정하며, 낙찰자는 의존성을 선언해 하위 작업을 다시 위임한다. 독립 작업은 병렬 수행하고 결과를 통합한다. 고정 관리자 에이전트와 장기 메모리는 없다.

최신 실험은 **5개 복합 작업 × 3조건 × 3회**다. HTTP 429 복구를 포함해 45개 작업 모두 문서를 생성했고 필수 facts는 44개가 통과했다. CSV는 원래 실패를 포함한 61개 실제 시도를 보존한다. 보고서 표는 중복 없이 완료된 45개 작업을 집계한다.

## 설치와 검증

저장소 루트에서 실행한다. Python 3.10 이상이며, 개발 의존성은 JSON Schema 검증용이다.

```bash
python3 -m venv submissions/26622007/.venv
source submissions/26622007/.venv/bin/activate
python3 -m pip install -r submissions/26622007/week-03/extensions/peer_dag/requirements-dev.txt

# API 호출 없이 원본 로그·CSV·보고서 표·콘솔/JSONL 사본 일치 검사
python3 submissions/26622007/week-03/submission_results.py --check
python3 submissions/26622007/week-03/submission/compare_results.py --check
python3 -m unittest discover -s submissions/26622007/week-03 -p 'test_*.py' -v
python3 -m unittest discover -s submissions/26622007/week-03/extensions/peer_dag -p 'test_*.py' -v
python3 scripts/check_week03.py submissions/26622007/week-03
```

## 새 실험 실행

모델은 `deepseek/deepseek-v4.1-flash`, OpenRouter의 Fireworks 제공업체 고정, temperature=0, reasoning off다. 모든 단계에서 strict JSON Schema `response_format`을 사용하고 로컬 검증도 수행한다. 출력 토큰 상한 필드는 전송하지 않는다. 깊이 5, 동시 호출 3까지 허용한다.

API 키는 `OPENROUTER_API_KEY` 환경변수 또는 상위 학번 폴더의 로컬 `.env`로 읽는다. 실제 키 파일은 week-03 내부에 두거나 커밋하지 않는다. 아래 `run`은 실제 API를 호출하며 새 실험 ID를 만든다. 입력·설정·코드는 먼저 커밋해야 한다.

```bash
python3 submissions/26622007/week-03/extensions/peer_dag/suite_study.py plan
python3 submissions/26622007/week-03/extensions/peer_dag/suite_study.py run
```

실행 결과는 `extensions/peer_dag/conditions/<실험 ID>/`, 원본 trace는 `extensions/peer_dag/logs/`, 산출물은 `extensions/peer_dag/runs/`에 저장된다. 제출 집계기는 명시된 20260922 실험을 재검증하므로 새 결과로 자동 교체하지 않는다.

## 근거와 이전 시도

- [집계 정의와 검증](submission/README.md): CSV 필드, 429 복구, 원본 콘솔·전체 협의 JSONL 사본.
- [기본 실험·직접 수행과 비교](submission/COMPARISON.md): 배정 정확도, 산출물 양, 관측 깊이·병렬도, 품질 한계.
- [현재 구현·프롬프트](extensions/peer_dag/README.md), [작업과 gold](TASK_DESIGN.md), [사례 입력](extensions/peer_dag/cases/README.md).
- [최초 실험 원본](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/FINAL_REPORT.md)과 [429 복구 원본](extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/recovery_429/REPORT.md)은 당시 집계 범위를 그대로 보존했다. 현재 제출 표의 합산 정의는 위 집계 문서를 따른다.
- [이전 기본 배정 보고서](REPORT_ALLOCATION_REFERENCE_20260922.md)와 [당시 CSV](results-allocation-reference.csv), [축약 전 상세 작성본](REPORT_DETAIL_20260922.md)은 과거 스냅샷이다. 당시 미완성 작성란은 현재 제출 보고서에서 대체했다.
- 과거의 출력 토큰 상한에 의한 잘림, 429 중단, 구조화 출력 실패, 장기 메모리 확장 시도는 원본 로그와 커밋에 보존했다. 현재 결과와 합쳐 성능을 주장하지 않는다.
