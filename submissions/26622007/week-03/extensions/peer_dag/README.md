# 작업자가 계획하고 서로 위임하는 DAG 실험

2026-09-22: 사용자의 요청에 따라 현재 설정과 실제 API payload의 출력 토큰 상한을 제거했다.
`max_tokens`/`max_completion_tokens`를 보내지 않으며 다른 숫자로 대체하지 않는다.
이전 45회는 과거 2200토큰 설정의 결과다. 2200은 사용자가 요청하지 않은 구현 에이전트의 설정이었고,
그로 인한 17회 잘림 때문에 28/45를 상한 없는 시스템의 성공률로 해석할 수 없다.
새 45회는 [별도 규약](conditions/SUITE_NO_TOKEN_LIMIT_PROTOCOL.md)과 ID로 완료했으며 과거 기록을 교체하지 않았다.

[복합 작업 목록 5개](cases/README.md)를 추가했다. 게임 기획과 코드 구조 설계를 함께 다루는 작업 등은
`cli.py list`로 확인하고 `cli.py live --case game-design-architecture`처럼 선택한다.
출력 상한 제거 후 [45회 최종 결과](conditions/20260922T012131-no-token-limit-9703d2/FINAL_REPORT.md)와
[산출물 및 품질 점검](conditions/20260922T012131-no-token-limit-9703d2/QUALITY_REVIEW.md)을 참고한다.
baseline 10/15, homogeneous 10/15, overconfident 9/15가 필수 facts 검사를 통과했다. 나머지 16회는 HTTP 429 소진 실패다.
본 실험의 실행 중첩은 16회(동일 Worker 중첩 7회), 실제 최대 깊이는 1이다.
[별도 429 복구 16회](conditions/20260922T012131-no-token-limit-9703d2/recovery_429/REPORT.md)는 문서 16개를 생성하고 facts 15/16을 통과했다. 실제 최대 깊이 2, 중첩 11회(동일 Worker 5회)다.
복구를 포함하면 원래 45개 슬롯 모두 문서를 확보했고 44개는 필수 facts가 맞다. 한 건의 값 의미 충돌을 포함해 원본과 정성 점검을 보존했다.
본 실험과 복구의 응답 총 829개는 잘림 없이 stop으로 종료됐으며 strict JSON Schema 검사를 통과했다. 이는 설계 문서의 내용 정확성과 별개다.
[과거 2200토큰 실험](conditions/20260921T113158-suite-ab4e67/FINAL_REPORT.md)의 통과 28/45와 잘림 17회는 별도 기록이다.
당시 고정 응답 직렬/병렬 재생 10회는 상태와 모든 산출물이 같았다. 새 45회 통계에 합산하지 않으며 실제 LLM 응답의 결정성을 뜻하지 않는다.
아래 과거 실험은 기존 출시 검토 한 사례의 기록이며 이번 5개 사례 결과와 합산하지 않는다.

기존 Contract Net의 필수 세 조건과 별개인 확장이다. 사용자가 제안한 구조를 구현한다.
모든 Worker는 입찰·계획·선정·실행·통합을 수행한다. 고정 manager 에이전트는 없다.
각 작업의 요청자가 동료들의 계획을 평가하고, 선정된 작업자가 자신의 하위 작업을 다시 맡긴다.
최초 요청자는 실행 인자로 바꿀 수 있다. 부모가 자식을 기다리는 동안 호출 슬롯을 점유하지 않는다.

모든 Worker의 모든 단계에 같은 **공통 팀 역할표**를 시스템 입력으로 전달한다.
역할표는 기존 역할 정의에서 생성한다: A는 제품·기술, B는 사업·분석, C는 운영·커뮤니케이션이다.
각 Worker는 본인과 동료의 전문성을 참고해 하위 작업의 목표와 검증 기준을 정하며,
독립 작업은 병렬 가능하게 분리하고 선행 결과가 필요한 작업만 depends_on으로 연결하도록 지시받는다.
같은 Worker도 독립 작업을 별도 세션에서 병렬 처리할 수 있다는 규칙을 함께 받는다.
역할표에 현재 부하, 다른 호출의 대화, 확신도나 평가 정답은 포함하지 않는다.
공통 역할표를 추가한 이후의 프롬프트는 과거와 다르므로 이전 로그의 정확한 재생에는 당시 소스 커밋을 사용해야 한다.
[역할표 전달 검증](TEAM_ROSTER_VERIFICATION.md)에 실제 계획과 제공업체 제한으로 중단된 실행을 구분해 기록했다.

## 이번 구현의 수행 범위

제공된 합성 출시 자료를 분석하고 실제 JSON 결과물을 작성한다. 작업을 할 수 있다는 입찰만으로 끝내지 않는다.
외부 시스템 조작·셸 실행·실제 배포는 하지 않는다. 역할 A/B/C의 전문성 설명은 기존 과제와 같다.
완성 결과의 수치·필수 판단은 모델에게 전달하지 않는 `expected.json`으로 사후 검사한다.
검사는 해당 항목의 일치만 보장하며 모든 문장의 품질이나 현실의 업무 수행 능력을 보장하지 않는다.

## 프로토콜과 상태

1. 작업 요청 → 각 Worker가 `bid`, `confidence`, `reason`, `plan` 제출.
2. plan은 직접 실행(`execute`) 또는 하위 계획(`delegate`). actions에는 실행·검증 절차를 적는다.
3. 요청 Worker는 confidence를 가린 계획을 coverage / feasibility / verification 각 0~2점으로 평가한다.
4. 모든 항목이 1점 이상인 후보 중 합계, confidence, 작업별 순환 우선순위 순서로 선정한다. 최초 작업의 동점 우선순위는 요청자부터 시작하고, 하위 작업은 승인된 steps 순서에 따라 요청자의 다음 Worker부터 순환한다. 실행 속도나 당시 부하에 따라 선정이 바뀌지 않는다.
5. 실행 계획은 별도 모델 호출로 수행한다. 위임 계획은 하위 작업의 DAG를 실행하고 선정 Worker가 결과를 통합한다.
6. 후속 작업에는 성공한 선행 작업의 결과만 전달한다. 실패는 해당 후속 작업을 blocked로 만들고 독립 작업은 계속한다.

각 하위 작업은 `id`, `goal`, `depends_on`, `acceptance`, `reads`, `writes`를 선언한다.
depends_on은 같은 계획 안의 작업 ID만 참조한다. 자기 참조·누락 참조·순환을 거절한다.
최종 통합에는 모든 하위 결과를 전달한다. 선언된 선행 작업이 모두 성공하면 바로 시작하며 전체 단계의 완료를 기다리지 않는다.
산출물은 작업 ID별로 분리한다. reads/writes는 논리적 자원 이름이며 실행·통합 중 read/write와 write/write 충돌을 막는다.
LLM이 누락한 의존성·자원은 코드만으로 알아낼 수 없으므로 계획 내용 검토는 여전히 필요하다.

## 제한과 재현

### API 구조화 출력 필수

[적용·실호출 검증 보고서](RESPONSE_FORMAT_VERIFICATION.md)에 수정 과정, 실패 원본, 최종 23회 요청 검사와 재생 결과를 정리했다.

모든 실제 peer 호출은 `response_formats.py`에서 만든 단계별
`response_format={"type":"json_schema","json_schema":{"name":...,"strict":true,"schema":...}}`를 전달한다.
propose는 입찰·계획·하위 작업 필드, review는 실제 후보 ID와 0~2 정수 점수,
execute/synthesize는 summary·스칼라 facts·evidence를 제한한다. 평가 답안을 스키마에 넣지 않는다.
기본 Contract Net 입찰에도 같은 방식으로 bid/confidence/reason 스키마를 전달한다.

공통 전송기는 형식 누락과 `require_parameters` 비활성화를 요청 전에 차단한다.
HTTP 400 등 미지원 응답에서 스키마를 제거하거나 JSON mode로 낮추지 않는다.
실제 직렬화 요청과 로그를 검사하며 재생도 messages와 response_format을 모두 대조한다.
이전의 스키마 없는 로그는 원본 소스 커밋에서 재생해야 한다.
20260921T051850-f6cc75의 9회 비교는 **API response_format 미적용 상태**의 기록이다.
새 설정으로 얻은 결과와 합쳐서 성능 향상을 주장하지 않는다.

제공업체별 지원과 강제 수준이 다르므로 `strict=true` 자체가 내용의 정확성이나 결정성을 보장하지 않는다.
로컬의 파싱·필드 타입·깊이·DAG·수치 검증을 유지한다. 동적 facts 키와 스칼라 값은
JSON Schema의 typed additionalProperties로 표현하므로 선택한 엔드포인트의 지원을 확인해야 한다.

현재 기본 입찰과 peer DAG의 제공업체는 `provider.only=["fireworks"]`로 고정한다.
자동 라우팅 실호출에서 Wafer의 JSON 형식 위반을 관찰했기 때문이다. 모델 ID는 유지한다.
허용 목록 밖의 제공업체로 전환하지 않으며 Fireworks가 사용 불가하면 실행을 실패로 기록한다.
[OpenRouter 문서](https://openrouter.ai/docs/guides/features/structured-outputs)는 엔드포인트별
강제 수준 차이를 명시하고, [Fireworks 문서](https://docs.fireworks.ai/structured-responses/structured-response-formatting)는
JSON Schema와 정규식 지원 및 일부 정규식의 제한을 설명한다. 로컬 검증은 계속 필요하다.
응답 본문은 2 MB와 90초 수신 기한을 검사한다. 기한 검사는 각 청크 수신 직후이므로
소켓 읽기 대기 30초가 추가될 수 있다. heartbeat로 무한 대기하지 않는다.

깊이, 전체 작업 수, 계획별 하위 작업 수, 모델 호출 수, 동시 호출 수를 제한한다.
현재 기본값과 두 확장의 `config.json`은 `max_depth=5`다. 루트가 깊이 0이므로
깊이 0~4에서 재위임할 수 있고 깊이 5에서는 직접 실행해야 한다. 지원 설정 범위는 0~5다.
이는 허용 상한이며 실제 모델이 매번 깊이 5까지 분해하도록 강제하지 않는다.
이 확장의 전체 작업 12개·계획별 하위 작업 4개·모델 호출 64회 제한은 함께 적용되므로
분기가 많으면 깊이 상한에 도달하기 전에 작업 또는 호출 예산으로 중단될 수 있다.
형식이 틀린 입찰은 거절하고 원문을 기록한다. 평가·실행 실패는 실패로 보존하며 조용히 다시 계획하지 않는다.
HTTP 재시도는 별도 고정 제한을 사용한다. 현재 429는 최대 6회 시도하고 15→30→60→60→60초 대기한다.
서버의 `Retry-After`(초 또는 HTTP 날짜)가 더 길면 그 최소 대기를 존중한다. 누적 대기 예산 300초를 넘기면
서버 안내보다 일찍 다시 요청하지 않고 실패를 기록한다. 일반 오류·타임아웃은 기존 최대 2회 시도다.
각 요청의 원문·스키마는 재시도 사이 바뀌지 않는다. 요청당 HTTP 상한은 6회이며 전체 모델 호출 상한은 64회다.
대화는 호출마다 새로 만들며 `(작업 ID, Worker)`별로 분리한다.
같은 C가 독립 작업 두 개에 선정되어도 각 작업의 C 호출은 병렬로 진행할 수 있다.
전체 동시 호출은 `max_parallel=3`으로 제한하며 같은 작업·Worker 세션은 동시에 호출하지 않는다.
선행 의존성, read/write 충돌, 부모의 하위 결과 대기는 기존과 같이 검사한다.
`concurrency_policy=task-worker-isolated-v1`을 실행 설정에 기록한다. 일시 중단한 연구·메모리 확장은 기존 Worker별 순차 정책을 유지한다.
[같은 Worker 병렬 검증](SAME_WORKER_PARALLEL_VERIFICATION.md)에서 C의 실제 동시 실행과 기존 응답 재생 결과를 확인할 수 있다.
확장 로그·결과물은 이 폴더 안에 저장한다. 최신 제출 보고서와 `results.csv`는 [제출 집계 규약](../../submission/README.md)에 따라 이 실험의 429 복구 포함 최종 결과를 내보낸다. 이전 기본 배정 결과는 별도 CSV로 보존한다.

`expected.json`의 평가 답안은 어떤 Worker의 요청에도 넣지 않는다. 과제 해석과 계획 타당성 검토는 사용자의 학습 부분이다.

## 실행

실행 코드는 Python 3.10 이상과 표준 라이브러리를 사용한다.
스키마 회귀 테스트에는 개발 의존성 `jsonschema`가 필요하다. 저장소 루트에서 실행한다.

```bash
# 개발 테스트 환경 (키를 담는 상위 학번 폴더에 설치, 과제 검사 경로 밖)
python3 -m venv submissions/26622007/.venv
source submissions/26622007/.venv/bin/activate
python3 -m pip install -r submissions/26622007/week-03/extensions/peer_dag/requirements-dev.txt

# API 호출 없이 구조와 재위임을 확인하는 모의 실행
python3 submissions/26622007/week-03/extensions/peer_dag/cli.py demo

# 깊이·작업 수·호출 예산 확인. API를 호출하거나 키를 출력하지 않는다.
python3 submissions/26622007/week-03/extensions/peer_dag/cli.py plan

# 실제 모델로 계획, 동료 선정, 분석 수행, 통합을 실행한다.
python3 submissions/26622007/week-03/extensions/peer_dag/cli.py live

# 같은 재귀 구조에서 전문성 또는 C의 입찰 확신도 지시만 바꾼다.
python3 submissions/26622007/week-03/extensions/peer_dag/cli.py live --condition homogeneous
python3 submissions/26622007/week-03/extensions/peer_dag/cli.py live --condition overconfident

# 다른 Worker를 최초 요청자로 사용한다. 고정 manager 프로세스는 없다.
python3 submissions/26622007/week-03/extensions/peer_dag/cli.py demo --requester C

# 기존 실행의 응답을 고정하고 스케줄러만 순차/병렬로 바꾼다.
# <원본.jsonl>에는 이 확장 실행으로 생성된 로그 경로를 넣는다.
python3 submissions/26622007/week-03/extensions/peer_dag/cli.py replay --replay <원본.jsonl> --parallel 1
python3 submissions/26622007/week-03/extensions/peer_dag/cli.py replay --replay <원본.jsonl> --parallel 3

# 기록된 선행 관계와 실제 호출 겹침을 독립 검사한다.
python3 submissions/26622007/week-03/extensions/peer_dag/audit.py <원본.jsonl>

# 확장만 검증한다. 기존 하네스 테스트는 별도 명령이다.
python3 -m unittest discover -s submissions/26622007/week-03/extensions/peer_dag -p 'test_*.py' -v
python3 -m unittest discover -s submissions/26622007/week-03 -p 'test_*.py' -v
```

키는 기존 상위 학번 폴더의 로컬 `.env` 또는 `OPENROUTER_API_KEY` 환경변수를 사용한다.
명시한 파일이 있으면 그 파일의 키를 사용하며, 유효한 키 형식이 없으면 API를 호출하지 않는다.
입력·설정·사후 평가 계약을 바꾸었다면 live 실행 전에 각각 커밋해야 한다.

모드별 결과는 `runs/<run-id>/result.json`, 각 작업 산출물은 `runs/<run-id>/artifacts/`에 저장된다.
모델 원문·입찰·평가·선정·작업 상태·HTTP 응답과 usage는 `logs/<run-id>.jsonl`에 남는다.
초기 두 실행은 경로 분리 수정 전이므로 산출물이 해당 run 폴더 바로 아래에 있다. 기존 파일은 이동하지 않았다.

`--condition` 기본값은 `baseline`이며 A/B/C의 전문성을 각각 유지한다.
`homogeneous`는 모든 단계에서 본인의 전문성 설명과 공통 역할표의 세 전문성을 모두 같은 범용 역할로 바꾼다.
`overconfident`는 C의 propose 단계 끝에 항상 입찰하고 confidence를 95 이상으로 반환하라는 지시만 추가한다.
평가·실행·통합 지시와 선정 규칙은 동일하다. 조건별 실제 로그를 replay할 때도 같은 `--condition`을 지정한다.

세 조건을 각각 3회 실행하려면 `python3 submissions/26622007/week-03/extensions/peer_dag/condition_study.py`를 사용한다.
[실험 규약](conditions/PROTOCOL.md)과 입력·코드를 먼저 커밋해야 한다.
[2026-09-21 비교 보고서](conditions/20260921T051850-f6cc75/REPORT.md)에 실제 9회 실행과 실패 원본, 통제 검사, 해석을 정리했다.

공통 역할표·strict JSON Schema·같은 Worker 병렬 정책을 적용한 기존 최종 실험은 소스 `5aa1a55`에서
[최종 규약](conditions/FINAL_PROTOCOL.md)에 따라 `condition_study.py --serial --gap-seconds 15 --protocol FINAL_PROTOCOL.md`로 실행한다.
실험 9개를 차례로 진행하며 각 실험 내부의 `max_parallel=3`은 유지한다.
[최종 실험 보고서](conditions/20260921T093041-84990d/FINAL_REPORT.md)에 9회 중 8회 성공,
HTTP 429로 인한 실패 1회, 핵심 결과 일치와 계획·문장 변동, 실제 C 병렬 실행 및 순차/병렬 재생을 기록했다.
이후 사용자가 요청한 429 재시도 보강과 실패 실행 재시도는 [별도 규약](conditions/RETRY_PROTOCOL.md)을 따른다.
현재 재시도 설정을 기존 9회의 고정 설정과 같다고 해석하지 않는다.
[재실행 결과](conditions/20260921-rate-limit-retry/REPORT.md)는 수행 완료·정답 11/12다.
실제 429는 없었고, 수익성 계산 오류가 후속 통합까지 전달된 원본을 보존했다.

## 파일 책임

| 파일 | 역할 |
|---|---|
| `core.py` | Task/Proposal 계약, 계획 검증, 동료 선정, 재귀 DAG 실행, 자원 lease, 사후 평가 |
| `models.py` | 단계별 프롬프트, 기존 OpenRouter 전송기 어댑터, 입력 해시를 확인하는 응답 재생 |
| `cli.py` | 설정·입력 고정, 실행 기록, 산출물 저장, 모델에 전달하지 않는 사후 평가 |
| `fixtures.py` | Python으로 만든 명시적 모의 응답과 중첩 재위임 예제 |
| `audit.py` | 원본 로그에서 의존성·작업별 세션·자원 충돌·종료 상태와 Worker별 실제 동시 호출 검사 |
| `test_peer_dag.py` | 실패 전파, 교착 방지, 실제 execute 동시성, 동일 응답의 순차/병렬 결과 일치 등 |

## 보장 범위와 남은 학습 질문

- `succeeded`는 계획된 모델 단계와 결과 JSON 생성이 완료됐다는 뜻이다. 하위 작업 내용 전체가 정답이라는 의미는 아니다. 최종 `evaluation.passed`는 사후 12개 항목의 일치를 별도로 표시한다.
- 공통 팀 역할표, 원자료와 의존 산출물을 전달하며 Worker의 대화 이력은 호출 사이에 공유하지 않는다. 역할 정체성은 같아도 서로 다른 작업의 대화는 분리된다.
- 독립 작업은 같은 Worker가 선정돼도 분리된 세션에서 병렬 실행할 수 있다. 자원 충돌이나 선행 의존성이 있으면 대기한다. 배정 점수와 확신도 규칙은 바꾸지 않는다.
- graph 검사는 선언된 의존성의 구조를 검사한다. 중요한 의존성을 LLM이 빠뜨린 경우나 사실 검증 기준 자체의 부족함은 별도 검토가 필요하다.
- 요청 Worker가 자신의 제안도 평가할 수 있다. 자기 제안 선호와 계획 점수의 변동은 아직 통제하지 못한 요인이다. 확신도도 능력의 교정된 확률이 아니다.
- 순환 동점 처리는 해당 작업의 계획 순서에 고정된다. 작업 나열 순서를 바꾸면 동점 배정도 달라지므로 같은 계획으로 비교해야 한다.
- replay는 같은 입력에 기록된 응답을 주입하는 스케줄러 검증이다. 실제 LLM 호출의 결정성이나 품질 향상을 입증하지 않는다. `--replay-delay-ms`는 인공 지연이므로 그 실행 시간을 API 속도 개선으로 해석하지 않는다.
- 단일 실제 사례는 동작 확인이다. 성능 비교에는 같은 사례 집합·모델·평가 기준으로 반복 실행하고 결과 품질, 완료 시간, 비용과 실패를 함께 집계해야 한다.
- 필수 세 조건의 기존 실험은 그대로 남는다. 확장 결과로 기본 `homogeneous`/`overconfident` 반복 횟수나 보고서의 사용자 해석을 대체하지 않는다.

## 관찰된 개선 과정

첫 실제 실행(`20260917T015243-live-1a4baa3f`)은 모델 20회 호출로 12개 사후 항목을 통과했다.
응답 비용 합계는 $0.01916959, 소요 시간은 153.031초였다. 하지만 모든 후보가 같은 점수와 확신도를 받았고 ID 동점 규칙 때문에 모든 작업이 A로 몰렸다.
입찰은 최대 3개가 겹쳤으나 실제 결과 생성 호출은 최대 1개였다. [초기 감사](logs/07-live-initial-audit.json)에 차이를 보존한다.

이후 승인된 하위 계획 순서에 따른 순환 동점 처리를 추가했다. 부하나 도착 시간에 의존하지 않는 선택이므로 같은 응답의 순차·병렬 재생이 가능하다.
[모의 실행 감사](logs/11-demo-rotation-audit.json)에서 실제 결과 생성 호출 2개가 겹쳤고, 6개 작업과 재위임 A→B→A가 완료됐다. 이는 모의 실행 증거다.

구체적인 실제 최종 검증은 `VERIFICATION.md`에 기록한다.
