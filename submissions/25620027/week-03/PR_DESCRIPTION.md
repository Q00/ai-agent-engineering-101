# [week-03] 25620027

자연어 행정업무 6개를 배정하는 Contract Net을 구현했습니다. 입찰 생성은 동일 모델의 독립 컨텍스트 3개에 맡기고, 공고·형식/단계 검사·선정·저장·평가는 결정론적 코드로 고정했습니다. 같은 프로토콜에서 역할 구분과 과신 지시가 배정에 미치는 영향을 비교했습니다.

## 1. 설계 v2.1

![Contract Net 아키텍처 v2.1](https://raw.githubusercontent.com/SungJinho/ai-agent-engineering-101/week-03/submissions/25620027/week-03/architecture-v2.1.png)

[확대용 SVG](https://github.com/SungJinho/ai-agent-engineering-101/blob/week-03/submissions/25620027/week-03/architecture.svg) · [Excalidraw 원본](https://github.com/SungJinho/ai-agent-engineering-101/blob/week-03/submissions/25620027/week-03/architecture.excalidraw) · [그림에서 코드로 읽기](https://github.com/SungJinho/ai-agent-engineering-101/blob/week-03/submissions/25620027/week-03/ARCHITECTURE.md)

- Manager 1개, Contractor A(계산)·B(글쓰기)·C(Python 코드) 3개로 구성했습니다. 각 호출은 자기 역할과 현재 공고만 받습니다.
- `gold`는 실행기에 전달하지 않고 사후 평가에만 사용합니다. 태스크·gold는 첫 API 호출 전 `7cc2955`로 커밋했습니다.
- 유효한 `bid=true` 중 최고 confidence를 선정하고, 최고점 동점이면 먼저 접수한 후보를 선정합니다. A→B→C 순차 호출이며 후보가 없으면 미배정입니다.
- 계약 상태와 공식 메시지·진단 이벤트를 SQLite에 보존합니다. JSON/권한/단계/마감/중복 검사는 수행하지만 confidence의 의미적 타당성을 gold로 보정하지 않습니다.
- Manager에 추가 LLM 판단을 넣지 않은 이유는 입찰 판단의 변화가 배정에 주는 영향을 분리해서 보기 위해서입니다. 실제 업무 수행·재위임·학습 메모리는 이번 구현 범위 밖입니다.

## 2. 실험 결과

OpenAI `gpt-4.1-nano-2025-04-14`, temperature=0, 동일 태스크·선정 규칙으로 각 조건을 3회 실행했습니다. 과신형은 baseline의 C에게만 항상 입찰·confidence 95 이상 지시를 추가했습니다.

| 조건 | 각 실행 correct | gold 일치 합계 | 메시지 평균 | 미배정 합계 | 오배정 합계 |
|---|---|---:|---:|---:|---:|
| baseline | 5, 5, 5 / 각 6개 | 15/18 | 29.67 | 3 | 0 |
| homogeneous | 1, 2, 3 / 각 6개 | 6/18 | 37.00 | 2 | 10 |
| overconfident | 2, 2, 2 / 각 6개 | 6/18 | 34.00 | 0 | 12 |

유료 실험의 162회 호출에서 API·JSON 형식·timeout 오류는 없었습니다. 무료 GLM/Nemotron의 완료 2회·실패 5회도 별도로 보존하며 위 평균에는 합치지 않았습니다. 사용량 환산 비용은 약 $0.00547입니다.

## 3. 해석

기본형에서도 계산 담당자가 T02를 거절해 미배정이 반복됐고, 일반형은 입찰 변동과 고정된 동점 순서의 영향을 받았습니다. 과신형 C는 세 번 모두 모든 업무를 가져갔습니다. 저장된 유효 후보에 기존 선정 함수를 재적용하면 54/54개 계약이 원래 결과와 일치하지만, 이 재현성이 입찰의 진실성을 보장하지는 않습니다. 결정론적 하네스는 절차를 통제하고 근거를 추적하게 해 주며, 의미적 판단의 적절성은 별도 검증이 필요하다는 결론입니다. temperature=0에서도 실행 결과가 달랐지만, 공고의 계약 ID·마감시각도 달라지므로 그 원인을 모델 내부 무작위성 하나로 단정하지 않습니다.

## 4. 제출물과 검증

- [REPORT.md](https://github.com/SungJinho/ai-agent-engineering-101/blob/week-03/submissions/25620027/week-03/REPORT.md): 설계·실행 설정, 전체 CSV 결과표, Smith(1980) 센싱 비교표, 원본 로그 행에 근거한 해석.
- `tasks.json`, Python 구현, `results.csv`, `logs/`, `uv.lock`, `SETUP.md`, 설계 PNG/SVG/Excalidraw를 포함합니다.
- 테스트 38개, Ruff, basedpyright, 공식 `check_week03.py` 통과. 원본 로그 사본·SQLite 집계·실행 소스 해시를 대조했습니다.
- API 키·개인 환경 파일은 포함하지 않았습니다. 변경은 `submissions/25620027/week-03/` 안에 한정됩니다.
