# Week 03 — LLM의 자기 확신도로 행정업무를 배정하면

> **구현 완료·실험 미완료(2026-09-16 14:38 KST).** 무료 API의 HTTP 429로 중단됐다. 완료된 비교 실행은 baseline 1회·homogeneous 1회이며, overconfident는 중단 기록만 있다. 조건별 3회 요건을 충족하지 못했으므로 현재 파일은 제출 완성본이 아니다.

## 1. 실험 설정과 재현

가상 행정업무 6개를 계산 A·글쓰기 B·코드 C에 두 개씩 대응시켰다. [tasks.json](tasks.json)은 첫 API 요청 전에 커밋했다(`7cc2955`). 태스크 순서는 T01→T03→T05→T02→T04→T06이다. 실제 일을 수행하는 정답률이 아니라 **사전 지정한 담당자와 배정 결과의 일치**를 측정한다.

기존 무료 설정 GLM-5.2는 첫 요청부터 429를 반환했다. 날짜 경계 뒤의 재시도도 같은 오류여서 두 시도를 보존하고, 공식 과제 예시의 Nemotron 무료 모델로 별도 실험을 시작했다. 완료된 조건 비교에는 Nemotron만 사용한다.

| 항목 | 비교 실험 설정 |
|---|---|
| 제공자·모델 | OpenRouter · `nvidia/nemotron-3.5-lightning:free` |
| 공통 생성 설정 | temperature=0, max_tokens=1024 |
| 호출 | A→B→C 순차, 현재 system/user 두 메시지만 전송, 자동 재시도 0 |
| 시간 설정 | SDK timeout 60초, 계약 입찰 창 240초, 각 호출 전 간격 4초 |
| 조건·반복 | baseline→homogeneous→overconfident 순환, 조건별 완료 3회 목표; 현재 1/1/0회 완료 |
| 선정 | 유효 bid=true 중 최고 confidence, 최고점 동점은 먼저 접수한 후보 |
| 형식 오류 | JSON 객체 전체를 엄격히 파싱. 코드블록·추가 필드·문자열 bool·범위 밖 confidence는 거절 |
| 메시지 | 태스크별 공고 3 + 유효 true 입찰 수 + 낙찰 0/1 |
| 상태 저장 | 동일한 로컬 SQLite 하네스, gold는 실행기에 전달하지 않고 사후 평가에만 사용 |
| 도구 버전 | Python 3.12.11, OpenAI SDK 3.8.0; 전체 의존성은 uv.lock, 실행별 로그에 SDK 버전 기록 |

**시간 제한의 범위:** SDK timeout은 호출 전체의 단단한 60초 상한이 아니다. 실제 baseline 1의 단일 호출은 최대 107.0초 걸렸다. 계약 마감은 별도 단조 시계로 검사하며, 응답이 마감 이후 도착하면 후보에서 제외하고 원문을 보존한다. 동일 설정을 모든 조건에 적용한다.

[프롬프트 전문](cnp/prompts.py)은 공통 JSON 지시와 역할 지시로 구성한다. baseline은 계산/글쓰기/코드로 구분하고, homogeneous는 모두 일반 문제 해결 역할로 바꾼다. overconfident는 baseline의 C에게만 항상 입찰하며 confidence 95 이상을 내도록 지시한다. 응답을 받은 뒤 bid나 점수를 보정하지 않는다. 실제 사용 프롬프트는 각 로그의 PROMPT A/B/C에 보존한다.

```bash
cd submissions/25620027/week-03
./run_lab.sh --config config-nemotron.json --output experiments/nemotron-free --runs 3
uv run --frozen python collect_results.py
```

키는 제출하지 않는다. 실행·재개·다른 컴퓨터의 환경변수 설정은 [SETUP.md](SETUP.md), 도면과 함수의 대응은 [ARCHITECTURE.md](ARCHITECTURE.md)를 참조한다. 모델·프롬프트·태스크·설정·구현 SHA는 각 experiment.json에 고정된다.

## 2. 실제 결과

run 식별자는 `실험 이름:원본 실행 번호`다. GLM 실패는 전환 전 시도이며 Nemotron의 조건 효과 집계에 합치지 않는다. 공란은 중단 실행의 미집계 값으로, 0점이나 미배정 6건을 뜻하지 않는다.

<!-- RESULTS_START -->
| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---:|---:|---:|---:|---:|---|
| glm-free:1 | baseline |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
| glm-free:2 | baseline |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
| nemotron-free:1 | baseline | 6 | 5 | 31 | 1 | 0 | parse_fail=7; api_error=0; timeout=0 |
| nemotron-free:2 | homogeneous | 6 | 2 | 36 | 0 | 4 | parse_fail=6; api_error=0; timeout=0 |
| nemotron-free:3 | overconfident |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
| nemotron-free:4 | overconfident |  |  |  |  |  | crashed: BatchBlockedError: HTTP 429 |
<!-- RESULTS_END -->

원본: [결과 CSV](results.csv), [GLM 실패 1](logs/run-001-baseline.txt), [실패 2](logs/run-002-baseline.txt), [Nemotron 원본 실행](experiments/nemotron-free/runs/).

### 완료 횟수와 남은 실행

| 조건 | 완료 | 추가 완료 필요 |
|---|---:|---:|
| baseline | 1 | 2 |
| homogeneous | 1 | 2 |
| overconfident | 0 | 3 |

실행 명령을 다시 수행하면 실패를 삭제하지 않고 부족한 **7개 완료 실행**을 추가한다(최소 126회 Contractor 호출). 재개 가능 시각은 429 응답만으로 확정할 수 없다. 과신 실행은 T06의 B 호출에서 멈췄으며 counts를 공란으로 유지했다. 2026-09-16 14:38 KST 재개도 첫 호출에서 429로 중단됐다(run 4). 누적 Nemotron은 54회 요청 중 52회 API 응답을 받았고, 그 52개 원응답의 `usage.cost` 합계는 0이다. JSON 형식 실패도 API 응답 성공에는 포함되므로 배정 성공과 구분한다.

## 3. Smith(1980)의 분산 센싱과 이번 재현

| 비교 축 | Smith(1980), 수업에서 소개한 분산 센싱 | 이번 구현 |
|---|---|---|
| 노드·역할 | 지역에 분산된 센서/컴퓨터, 작업별 manager/contractor 역할 전환 | 한 프로세스의 고정 Manager + 같은 모델의 독립 컨텍스트 3개 |
| 입찰 생성 | 위치·센서 목록 같은 능력 정보와 정해진 기준 | 역할 설명·공고로 생성한 bid·confidence·reason |
| 입찰의 신뢰 | 자격 조건과 기술적 노드 정보에 기반. 프로토콜 자체를 거짓 입찰 방지 증명으로 보지는 않음 | 프롬프트와 형식 검사만으로 확신도의 진실성을 보장하지 못함. gold 재심사 없음 |
| 배정 품질 | 분산 센싱 작업을 수행할 적합한 노드의 선택과 결과 보고 | 사전 gold와의 일치. 실제 업무 수행 결과는 평가하지 않음 |
| 협상 비용 | 공고·입찰·낙찰·결과 보고 등 노드 간 통신과 처리 | 과제 정의의 메시지 수. 실제 LLM 토큰·호출·지연은 원응답의 별도 정보 |
| 실패 양상 | 적격/가용 노드 부족, 응답 지연과 작업 수행 실패 등 | 무입찰·오배정·동점 편향·잘못된 JSON·timeout·API 429 |
| 구현 범위 | 재위임과 결과 보고를 포함한 분산 문제 해결 | 재위임 없이 6개 독립 작업의 배정까지 재현 |

출처: [3주차 강의 A1·A3](https://wpti.dev/ai-agent-engineering-101/week-03.html), [공식 과제 명세](https://github.com/Q00/ai-agent-engineering-101/blob/94bff56/weeks/week-03/README.md). 위 표는 수업 자료의 센싱 예와 이 코드의 범위를 비교한 것이며 원 논문의 전체 실험을 재현했다는 뜻은 아니다.

## 4. 해석

현재 완료된 Nemotron baseline 1회에서는 correct=5, messages=31, unassigned=1, misawards=0을 얻었다. 18개 응답 중 유효 입찰 8, 정상 불입찰 3, parse_fail 7이며, 공고 18 + 입찰 8 + 낙찰 5 = 31로 SQLite 기록과 독립 대조했다. T03에서는 A가 역할 밖이라고 거절했고 B·C가 파싱에 실패해 미배정됐다([baseline 원본](experiments/nemotron-free/logs/run-001-baseline.txt), 19·22·25·26행). 따라서 이 미배정은 모두가 능력 부족을 판단한 결과가 아니다. T02는 A·C 모두 confidence=100이었지만 먼저 접수된 A를 선택했다(39·45·46행). T06에서는 B가 “Writing Python function aligns with writing skill”이라는 이유로 90에 입찰했으나 C의 100이 이겼다(62·65·66행). 이번에 정답 담당자가 선정됐더라도 역할 해석과 확신도가 객관적 능력의 보증은 아니다. 일반형 첫 실행에서는 correct=2, messages=36, unassigned=0, misawards=4였다. T05와 T06에서 세 세션 모두 95에 입찰해 A가 낙찰됐고, 원래 코드 담당자 C와 어긋났다([homogeneous 원본](experiments/nemotron-free/logs/run-002-homogeneous.txt), 29~36·59~66행). 유효 입찰이 8→12로 늘고 낙찰이 5→6으로 늘어 메시지도 31→36이 됐다. 다만 일반형에서는 세 역할이 같으므로 사전 gold가 유일한 적합자라는 뜻은 약해진다. 이 점수는 실제 업무 수행 능력이 아니라 고정된 담당자 표와의 일치도다. 조건별 반복이 끝나지 않아 이 두 실행의 차이를 안정적인 조건 효과로 결론 내리지 않는다.

GLM의 앞선 두 시도는 첫 A 호출에서 `HTTP 429`와 `stop_batch=true`를 남겼고 낙찰까지 도달하지 못했다([실행 1](logs/run-001-baseline.txt)의 8~11행, [실행 2](logs/run-002-baseline.txt)의 8~11행). 이를 정상 불입찰로 바꾸면 모델이 역할에 맞지 않다고 판단한 것과 호출 자체가 거절된 것을 혼동하게 된다. 두 시도는 결과표에 남기되 Nemotron 조건별 평균에는 포함하지 않는다.

과신 실행은 T06의 B 호출에서 429가 발생해 완료 지표를 만들지 않았다([overconfident 원본](experiments/nemotron-free/logs/run-003-overconfident.txt), 62~64행). 중단 전 T03에서는 B·C가 모두 95로 입찰해 B가 이겼고(22·25·26행), T04의 C는 파싱 실패로 후보가 되지 못했다(55·56행). 높은 확신도를 지시해도 동점 규칙과 형식 유효성이 낙찰을 제한하는 사례지만, 이것을 과신 조건 전체의 효과로 일반화하지 않는다. 무료 API 가용성이 비교의 완성을 제한했으며, 같은 설정으로 남은 반복을 완료한 뒤 조건별 경향을 다시 판단해야 한다.
