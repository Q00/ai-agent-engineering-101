# 25620027 — Round 3 중간평가 제출 자료

공식 키트 위에 만든 **거래 기억을 활용하는 규칙 우선 하이브리드 에이전트**다. 코드는 거래 조건을 판정하고 모델은 필요한 문장 해석을 보조한다. 이전에 성사하거나 실패한 기록은 다음 거래와 운영 검토에 쓴다.

먼저 [통합 HTML 보고서](REPORT.html)를 열거나 [REPORT.md](REPORT.md)를 읽는다. 부품 설계, 기본 키트와의 차이, 개선 과정, 실제 정산·거절 로그와 결과를 정리했다. [architecture.pdf](architecture.pdf)는 한 페이지 구조도이며, [ARCHITECTURE.md](ARCHITECTURE.md)에 흐름과 실패 처리 해설이 있다.

## R3 결과와 자료 범위

11/18 09:59 마감 후 보존 공개 보드에서 학생 Class **2/46위 · 1.731점**이다. 판매 정산 57,555건, 구매 정산 20,960건이다. 마감 직전 23:47 관측과 다음 날 두 차례 보드의 점수·순위를 대사했다. 교수님의 별도 채점 저장 파일은 공개 경로에서 찾지 못했으며, 마감 후 변하는 자산 값과 구분했다. All 진단값과 Class 성적은 구분한다.

## 파일 안내

| 파일 | 내용 |
|---|---|
| [REPORT.md](REPORT.md) | 교수님 요구에 맞춘 보고서 본문 |
| [architecture.tex](architecture.tex), [architecture.pdf](architecture.pdf) | LaTeX 그림 원본·빌드 결과 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 그림의 구성 요소·입출력·정상·실패 흐름 |
| [logs/market-kit.log](logs/market-kit.log) | 실제 배포 머신의 마지막 회전 로그, 원문 유지 |
| [logs/submission-20261118/evidence.json](logs/submission-20261118/evidence.json) | 시점·점수 재구성·원본 해시·자료 한계 |
| [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) | 제출 요구별 현재 상태와 최종 대사 항목 |
| `harness_app.py`, `r3/`, `market_a2a/`, `tests/` | 변경 코드와 검사 |

서두에 공식 성적과 동일 제외 가정의 1위 환산을 요약하고 별점·총점 추이 그림을 배치했다. 부록 A에 마감 환산표와 히트맵, 부록 B에 평가 분포와 거래 조건 비교 그림을 수록했다. 이 제출물의 주된 설명은 에이전트의 설계·실행·개선 과정이다.

## 보존 자료만으로 결과 확인

```bash
python3 logs/submission-20261118/finalize_evidence.py
```

보존 자료로 보고서의 기준선·마감 대사와 별점 환산을 다시 계산한다. 모델 키·네트워크·시장 실행이 필요 없다. 통합 HTML과 로그 집계본도 함께 제공한다. HTML 생성기와 초기 로그 집계 스크립트를 다시 실행하려면 로컬에 보관한 원본 히트맵 템플릿·초기 관측 자료가 추가로 필요하다.

## 로컬 회귀와 구조도 빌드

```bash
uv venv --python 3.12.11 .venv
uv pip sync --python .venv/bin/python requirements-local.lock
.venv/bin/python -m pytest tests -q -p no:cacheprovider
xelatex -interaction=nonstopmode -halt-on-error architecture.tex
```

회귀 검사는 모델 fixture와 로컬 통신 경로를 사용한다. 외부 API 키 없이 실행하며, 로컬 소켓 권한이 필요할 수 있다. 실거래 실행과 등록·키·환경 설정은 문서 검토와 분리한다. 이 문서 작업에서 거래·정책·배포·커밋·PR은 변경하지 않았다.
