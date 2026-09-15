# Week 03 Contract Net

이 구현은 세 층을 분리한다.

1. **공식 재현:** manager가 A/B/C에 순서대로 공고하고, 각 contractor가 도구 없이
   한 번의 LLM 호출로 `bid`, `confidence`, `reason`만 생성한다. manager는
   `confidence_only`로 한 번만 낙찰한다.
2. **Week 01 + Week 02 결합:** 낙찰된 contractor는 모두 같은 도구 세트를 받고,
   선택한 `react` 또는 `plan_execute` harness로 태스크를 실제 수행한다.
3. **별도 확장:** 세 입찰을 동시에 보내 공통 마감까지 수집한 뒤
   `confidence_only`, `token_aware`, `reputation_aware`를 비교한다. 공식 CSV와
   확장 CSV는 섞지 않는다.

manager와 monitor는 LLM 역할극이 아니라 결정적 제어 코드다. manager가 낙찰을
결정하고 monitor는 결과 검증과 이력 집계만 하므로, monitor가 낙찰에 직접
개입하지 않는다.

## 파일

| 파일 | 역할 |
|---|---|
| `tasks.json` | 실행 전 고정한 6개 태스크와 gold, 결정적 검증 규칙 |
| `agent_tools.py` | 모든 contractor가 공유하는 Week 01식 도구와 경로 보호 |
| `model_client.py` | OpenAI-compatible 대화·도구 호출·토큰 계측 어댑터 |
| `agents.py` | 엄격한 입찰 JSON, ReAct, Plan-Execute |
| `contract_net.py` | manager, 비동기 마감, 세 낙찰 정책 |
| `monitor.py` | 작업 검증, 이전 작업 토큰, 도메인별 평판 |
| `run_experiment.py` | append-only CSV와 실행별 JSONL 로그 |
| `test_lab.py` | API 없이 실행하는 프로토콜·가상 시간 테스트 |

## 도구와 개입 지점

모든 contractor는 `calculator`, `read_file`, `count_pattern`, `check_python`,
`write_note`를 동일하게 받는다. 파일 경로는 이 제출 디렉터리 안으로 제한한다.
`write_note`는 부작용이 있으므로 기본 배치 실행에서는 거절하고 intervention을
증가시킨다. 정말 필요한 별도 실험에서만 `--allow-write-tools`를 명시한다. 현재
고정 태스크는 쓰기 도구를 요구하지 않는다.

## 설치와 오프라인 검증

```bash
cd submissions/26510124/week-03
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python test_lab.py
```

API 키는 파일에 쓰지 않고 환경변수로만 설정한다.

```bash
export OPENAI_API_KEY=<key>
export AGENT_MODEL=gpt-5.4-mini
```

OpenRouter를 쓸 때만 `OPENAI_BASE_URL`을 추가한다. provider나 모델을 바꾸면 같은
비교 묶음 전체에 동일하게 적용하고 REPORT에 별도 실험으로 기록한다.

## 공식 실험

공식 결과는 순차 입찰, `confidence_only`, 동일한 모델·temperature·harness로
세 조건을 각각 3회 실행한다.

```bash
python run_experiment.py base \
  --runs 3 \
  --harness react \
  --model gpt-5.4-mini \
  --temperature 0.2 \
  --reasoning-effort none
```

이 명령은 `results.csv`에 9줄을 append하고 `logs/`에 9개 로그를 만든다.
실행 중 오류가 나도 해당 행과 로그는 보존한다. 일부 조건만 추가 실행하려면
`--condition baseline`처럼 지정할 수 있다.

## 확장 실험

확장에서는 각 태스크 안의 A/B/C 입찰만 병렬화한다. 서로 다른 태스크는 순차로
처리하여 검증된 이력이 다음 태스크에 반영되게 한다.

```bash
python run_experiment.py extended \
  --runs 3 \
  --condition overconfident \
  --harness react \
  --model gpt-5.4-mini \
  --temperature 0.2 \
  --reasoning-effort none \
  --bid-timeout 60
```

정책 하나만 재실행할 때는 `--policy token_aware`를 사용한다. 결과는
`extended_results.csv`, 로그는 `extended_logs/`에 저장된다. manager와 monitor가
결정적 코드이므로 각자의 LLM 토큰은 0이며, contractor 토큰에는 모든 입찰과
낙찰 후 실행 호출이 포함된다. `last_task_tokens`에는 입찰 비용을 제외한 가장
최근 낙찰 작업의 실제 수행 토큰만 들어간다.

## 제출 검사

레포 루트에서 실행한다.

```bash
python submissions/26510124/week-03/test_lab.py
python scripts/check_week03.py submissions/26510124/week-03
git diff --name-only upstream/main...HEAD
```

마지막 명령의 모든 경로가 `submissions/26510124/week-03/` 아래인지 확인한다.
