# Week 3 실습 검증본

작업을 배정한 다음 선정된 에이전트가 실제 도구로 수행하는 Contract Net.
Python 3 표준 라이브러리만 필요하다. 설계와 Mermaid는 [PLAN.md](PLAN.md).

## 1. 시스템 아키텍처

매니저와 평가기는 일반 코드다. 세 담당자 각각은 역할 프롬프트를 가진 LLM이다.
입찰에는 정답을 보내지 않으며, 선정된 담당자만 수행 루프를 시작한다.

## 2. 작업과 프로토콜

[tasks.json](tasks.json)은 계산·날짜 추출·숫자 정렬 각 2개다. `id, desc`는 공고용,
`gold, expected`는 평가 전용이다. 입찰은 `participate, confidence, reason`이다.
[contract_net.py](contract_net.py)의 parse_bid, choose, matches를 순서대로 읽는다.

## 3. 1주차 방식의 에이전트

[agent.py](agent.py)의 execute가 LLM → 도구 실행 → 관측을 반복한다.
최종 JSON 답변이면 종료하며 최대 호출은 6회다. 각 도구는 모델 없이 먼저 실행해 볼 수 있다.

```bash
cd submissions/21567/week-03
python3 -c 'from agent import calculate; print(calculate("17*23"))'
python3 -m unittest discover -p test_lab.py -v
```

## 4. Contract Net 연결

[contract_net.py](contract_net.py)의 run이 공고·입찰·선정·수행·평가를 연결한다.
[run.py](run.py)는 API 호출과 원본 로그, CSV 저장을 담당한다.
API 키는 환경 변수만 사용한다. OPENAI_BASE_URL은 해당 키의 제공자 주소,
AGENT_MODEL은 그 제공자가 지원하는 모델이어야 한다. 키를 코드나 로그에 넣지 않는다.

```bash
python3 run.py --provider openrouter --smoke --limit 1 --model "$AGENT_MODEL"
```

작업 하나의 로그에서 announcement 3개 → bid 3개 → award 1개 → tool → answer → evaluation을 확인한다.
도구 사용 여부는 로그에서 확인하며, 단순 정답 출력만으로 도구 호출이 있었다고 간주하지 않는다.
실제 모델은 도구 사용 지시를 따르지 않을 수 있다.

## 5. 과제: 조건 비교

연결 검증이 성공한 후 동일 모델로 아래를 실행한다.

```bash
for condition in baseline homogeneous overconfident; do
  for attempt in 1 2 3; do
    python3 run.py --provider openrouter --condition "$condition" --model "$AGENT_MODEL"
  done
done
```

results.csv는 원 과제의 배정 지표, execution.csv는 수행 지표다.
원본 실험 로그는 logs/, 연결 확인과 자동 테스트 기록은 smoke/에 분리한다.
REPORT.md에는 실제 결과가 생긴 뒤 표와 로그 근거를 추가한다.

## 현재 검증 상태

- 자동 테스트 14개 통과. 응답 형식 수정과 도구 루프, 조건 통제, 실패 기록을 검증한다.
- OpenRouter의 nvidia/nemotron-3.5-lightning:free 모델로 실제 단일 작업 검증 성공.
  smoke/20260915T094610-2e5b4232.jsonl에서 입찰 3개, 배정, calculate 도구 실행,
  최종 답 391과 success=true를 확인할 수 있다.
- 앞선 인증 실패, 잘못된 JSON 입찰, 도구 생략, 객체로 감싼 최종 답변,
  제공자의 응답 누락도 원본 로그와 커밋에 보존했다.
- 세 조건 각 3회 실험은 진행 중이다. 실제 완료 여부와 지표는 results.csv와 logs/를 기준으로 확인한다.

## 재현 설정

OpenRouter는 `--provider openrouter`로 선택한다. `OPENROUTER_API_KEY`를 사용하고
다른 OPENAI_BASE_URL 설정은 무시한다. 모델은 `nvidia/nemotron-3.5-lightning:free`,
temperature=0, max_tokens=1024, reasoning.enabled=false다.
입찰에는 response_format=json_object를 요청한다. 첫 수행 호출은 tool_choice=required,
관측 이후는 auto다. 최종 답이 문장이나 객체면 정답 정보 없이 형식 수정만 요청한다.
최대 수행 호출 6회는 수정 요청에도 적용된다. 정답 자체가 틀리면 재시도하지 않는다.

키는 실행 환경으로 전달한다. 파일에 저장하지 않는다. 3개 조건은 독립 실행이며
CSV append에는 잠금을 사용해 같은 파일에 기록하는 행이 섞이지 않게 한다.
