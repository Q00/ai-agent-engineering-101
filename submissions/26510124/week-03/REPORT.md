# Week 03 — Contract Net 실험 보고서

> 상태: 구현과 오프라인 검증은 완료했다. 실제 API 실행은 외부 전송 승인 후
> 수행하며, 아래 결과표와 해석은 생성된 로그를 근거로 갱신한다. 실행하지 않은
> 값을 임의로 채우지 않았다.

## 1. 실험 설정

provider는 OpenAI-compatible Chat Completions, 모델은 `gpt-4o-mini`, temperature는
`0.2`로 고정했다. 실행 환경에서 `OPENAI_BASE_URL`을 지정하지 않으면 OpenAI를
사용하고, API 키는 환경변수로만 전달한다. 사용 SDK는 `openai==3.14.0`이고 공통
도구 세트 버전은 `week03-tools-v1`이다. provider 내부 스케줄링과 구현 세부 사항은
직접 통제할 수 없다.

공식 실험의 입찰 system prompt는 contractor ID와 조건별 능력 문장 뒤에 다음
계약을 동일하게 붙인다.

```text
Return exactly one JSON object and no Markdown or prose:
{"bid": true, "confidence": 0, "reason": "one short sentence"}
bid must be a JSON boolean, confidence a number from 0 through 100, and
reason a non-empty sentence. Judge only whether you should perform the task.
Do not attempt the task and do not invent IDs, history, or token counts.
```

`baseline`은 A=계산, B=글쓰기, C=Python 코드로 구분했다. `homogeneous`는 세
contractor 모두 같은 범용 능력 문장으로 바꾸었다. `overconfident`는 baseline을
그대로 두고 C의 입찰 prompt에 모든 태스크에 95–100 확신도로 입찰하라는 문장만
추가했다. 태스크·모델·temperature·실행 harness는 조건 사이에 바꾸지 않는다.

공식 실행 명령은 다음과 같다.

```bash
python run_experiment.py base --runs 3 --harness react \
  --model gpt-4o-mini --temperature 0.2
```

낙찰된 contractor는 `calculator`, `read_file`, `count_pattern`, `check_python`,
`write_note`를 받으며 Week 02의 검증 턴이 있는 ReAct로 수행한다. 쓰기 도구는
기본적으로 승인을 받지 못한 것으로 처리한다. monitor는 계산 exact match,
글쓰기 형식·키워드, 코드 격리 단위 테스트로 결과를 검증한다. 이러한 수행과
검증 결과는 평판 이력에 쓰지만 공식 `correct`는 오직 낙찰자와 사전 고정한
`gold`의 일치로 센다.

## 2. 공식 실험 결과

아래 표는 실제 `results.csv` 생성 후 그대로 옮긴다. 현재는 외부 API 실행 전이라
관측값이 없다.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---:|---|---:|---:|---:|---:|---:|---|
| — | 실행 전 | — | — | — | — | — | 결과를 만들지 않음 |

## 3. Smith의 시스템과 이번 재현 비교

| 비교 항목 | Smith의 센서 네트워크 | 이번 LLM 실험 |
|---|---|---|
| 참여자 | 문제를 분산 처리하는 manager와 문제 해결 노드 | 결정적 manager, 세 LLM contractor, 결정적 monitor |
| 입찰 생성 방식 | 노드가 명시된 적합성 정보와 로컬 상태로 계산 | LLM이 역할 prompt와 공고를 읽고 bid·자기 확신도·이유를 판단 |
| 입찰 내용의 진실성 보장 | 설계된 평가 규칙과 공유된 프로토콜 가정에 의존 | 자기 확신도를 보증할 장치가 없고 과신 prompt로 쉽게 왜곡 가능 |
| 좋은 배정의 판단 기준 | 필요한 능력을 가진 노드에 작업을 배정하는 것 | 낙찰자와 사전 고정한 gold의 일치; 수행 성공은 별도 검증 |
| 협상 비용 | 공고·입찰·낙찰 메시지와 분산 계산 시간 | 같은 메시지에 LLM 토큰, latency, JSON 파싱 실패 비용이 추가 |
| 주요 실패 방식 | 공고 미수신, 부적절한 적합성 평가, 자원 변화 | 과신 입찰, 동질화에 따른 구별력 상실, parse fail, timeout, 검증 실패 |

## 4. 결과 해석

실제 9회 실행 후 조건별 집계와 로그의 `auction_id`를 근거로 한 문단으로 작성한다.
특히 baseline에서 전문 역할의 확신도가 gold 배정을 도왔는지, homogeneous에서
확신도 차이의 의미가 약해졌는지, overconfident C가 능력 밖 태스크를 가져가
`misawards`를 늘렸는지를 숫자와 실제 `bid_response`/`award` 행으로 확인한다.
현재는 실행 전이므로 어느 조건이 어떤 metric을 움직였다고 단정하지 않는다.

## 5. 별도 확장 실험 설계

확장은 공식 순차 재현을 바꾸지 않고 `extended_results.csv`에 따로 기록한다.
각 태스크에서 세 입찰을 동시에 시작하고 공통 마감까지 완전한 응답을 모은 뒤
후보를 고정한다. 빠른 낮은 점수에 조기 낙찰하지 않으며 마감 후 응답은 기존
낙찰을 바꾸지 않는다. 동점은 완전 응답 수신 시각, 그마저 같으면 A/B/C 고정
순서를 사용한다.

| policy | 점수 |
|---|---|
| `confidence_only` | $c_i$ |
| `token_aware` | $0.8c_i + 0.2e_i$ |
| `reputation_aware` | $0.6c_i + 0.2e_i + 0.2r_{i,d}$ |

`last_task_tokens`는 monitor가 가장 최근 낙찰 작업의 수행 usage에서 복사하고,
LLM은 생성하지 못한다. 비교 가능한 서로 다른 측정값이 둘 미만이거나 모두 같으면
모든 후보의 $e_i$를 0.5로 둔다. 이력이 없는 도메인의 평판은
$(successes+1)/(attempts+2)=0.5$에서 시작한다. monitor는 검증 후 다음 태스크부터
이력을 갱신하며 현재 낙찰을 되돌리지 않는다.

확장 결과표와 `quality_per_1k_tokens` 해석도 실제 실행 이후에만 추가한다.
