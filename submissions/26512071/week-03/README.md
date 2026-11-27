# Week 03 — LLM 계약자 기반 Contract Net

하나의 규칙 기반 매니저와 세 명의 LLM 계약자가 Contract Net Protocol로 태스크를
배정하는 실험이다. 계약자의 전문성 프롬프트를 바꿨을 때 배정 정확도가 어떻게
달라지는지 `baseline`, `homogeneous`, `overconfident` 세 조건에서 비교했다.

## 실험 구성

| 항목 | 설정 |
|---|---|
| API 제공자 | OpenAI API |
| 모델 | `gpt-5.6-luna` |
| temperature | `0.0` |
| reasoning effort | `none` |
| 태스크 | 6개 |
| 반복 | 조건별 3런, 총 9런 |
| 계약자 | `coder`, `analyst`, `writer` |
| 낙찰 규칙 | `bid=true` 중 최고 confidence, 동점은 coder → analyst → writer |

## 조건과 결과

| 조건 | 계약자 프롬프트의 차이 | correct | misawards | unassigned |
|---|---|---:|---:|---:|
| baseline | 서로 다른 전문성 | 18/18 | 0 | 0 |
| homogeneous | 세 명 모두 같은 generalist 전문성 | 6/18 | 12 | 0 |
| overconfident | baseline + coder의 과신 지시 | 16/18 | 2 | 0 |

baseline에서는 전문성 정보가 입찰을 구분해 모든 태스크가 올바르게 배정됐다.
homogeneous에서는 여러 계약자가 같은 confidence로 입찰하면서 고정 동점 규칙이
배정을 지배했다. overconfident에서는 coder가 analyst와 같은 confidence를 보고한
두 번의 런에서 데이터 태스크를 가져갔다. 모든 런에서 파싱 실패와 timeout은 0이었다.

## 파일 구성

| 파일 | 내용 |
|---|---|
| `contract_net.py` | 조건 생성, 동시 입찰, JSON 검증, 낙찰과 기록 |
| `tasks.json` | 태스크 설명과 gold 계약자 |
| `results.csv` | 9개 런의 측정값 |
| `logs/` | 공고·입찰 원문·낙찰을 담은 런별 로그 |
| `REPORT.md` | 설정, 프롬프트, 결과, Smith(1980) 비교와 해석 |
| `ARCHITECTURE.md` | 구현 구조와 메시지 흐름 |

## 실행

```bash
cd submissions/26512071/week-03
python3 -m venv /tmp/agent-ai-week03-venv
source /tmp/agent-ai-week03-venv/bin/activate
python -m pip install -r requirements.txt

unset OPENAI_BASE_URL
export AGENT_MODEL=gpt-5.6-luna
export AGENT_REASONING_EFFORT=none
read -s "OPENAI_API_KEY?OpenAI API key: "
export OPENAI_API_KEY
echo

python contract_net.py --runs 3
```

이미 `results.csv`에 9개 런이 기록되어 있으므로 제출 결과를 보존하려면 위 실험을
다시 실행하지 않는다. 새 실험은 기존 CSV에 행을 추가한다.

## 검증

저장소 루트에서 다음 명령을 실행한다.

```bash
python3 scripts/check_week03.py submissions/26512071/week-03
```

현재 제출본은 week-03 구조 검사를 모두 통과한다.
