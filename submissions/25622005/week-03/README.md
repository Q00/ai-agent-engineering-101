# Week 03 — Contract Net 사용법

manager 1명 + contractor 3명(A/B/C)으로 Smith 1980의 공고·입찰·낙찰을 재현하고,
세 조건에서 배정 품질과 협상 비용이 어떻게 달라지는지 잰다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `contractor.py` | 프롬프트, `build_team()`, 모델 호출, 입찰 JSON 파싱, `Meter` |
| `manager.py` | `run_round()` — 공고 → 입찰 수집 → 낙찰, 지표 집계 |
| `run.py` | CLI. 조건 하나를 1회 실행하고 `results.csv`에 한 줄 추가 |
| `tasks.json` | 태스크 6개. 각 `id` / `desc` / `gold` |
| `logs/` | 실행 1회당 콘솔 캡처 1개 |
| `REPORT.md` | 제출 리포트 (아직 골격) |

**독립변수는 `contractor.py`의 `build_team()` 한 곳에만 있다.** 조건을 바꿔도
그 함수 밖은 한 줄도 달라지지 않는다.

## 사전 준비

의존성은 저장소 루트의 `pyproject.toml`에 이미 있다 (`openai`, `dotenv`).

```bash
uv sync          # 또는: pip install openai python-dotenv
```

자격증명은 저장소 루트의 `.env`에 넣는다 (`.env`는 gitignore됨). provider는
week-02 starter와 같은 규칙으로 고른다 — `ANTHROPIC_API_KEY`가 있으면 Anthropic,
없으면 OpenAI 호환.

```bash
# Anthropic (이 제출물이 실제로 쓴 경로)
ANTHROPIC_API_KEY=<key>
ANTHROPIC_BASE_URL=<gateway url>      # 표준 엔드포인트면 생략 가능
AGENT_MODEL=claude-sonnet-5

# 또는 OpenAI 호환 / OpenRouter
OPENAI_API_KEY=<key>
OPENAI_BASE_URL=https://openrouter.ai/api/v1
AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
```

> ⚠️ **`AGENT_MODEL`은 반드시 설정할 것.** 비워 두면 `gpt-4o-mini`로 떨어진다.

API 키는 절대 커밋하지 않는다. CI가 `sk-` 접두사를 파일에서 찾는다.

## 실행

이 디렉터리에서 실행한다.

```bash
cd submissions/25622005/week-03
python run.py --condition baseline 2>&1 | tee logs/baseline-1.log
```

첫 줄에 provider, 모델, temperature, max_tokens가 찍히므로 로그 파일만 봐도
어떤 설정으로 돌린 것인지 알 수 있다.

조건은 `baseline` / `homogeneous` / `overconfident` 셋 중 하나. 조건당 3회, 총 9회를
돌린다.

```bash
for cond in baseline homogeneous overconfident; do
  for i in 1 2 3; do
    python run.py --condition "$cond" 2>&1 | tee "logs/$cond-$i.log"
  done
done
```

### 옵션

| 옵션 | 기본값 |
|---|---|
| `--condition` | (필수) |
| `--tasks` | `./tasks.json` |
| `--results` | `./results.csv` |
| `--run` | 비어 있으면 `results.csv`의 다음 번호 |

## 출력

**`results.csv`** — 실행마다 한 줄이 append된다. 파일이 없으면 헤더와 함께 만든다.

```
run,condition,tasks,correct,messages,unassigned,misawards,note
1,baseline,6,5,32,0,1,parse_fails=1 tokens=4820 calls=18
```

`note`에 `parse_fails`, 토큰, 호출 수가 들어간다. 실행이 도중에 죽으면 카운트는
빈칸으로, 에러는 `note`에 적힌 줄이 남는다. **그 줄을 지우지 않는다.**

**`logs/`** — `tee`로 남긴 콘솔 캡처. 모든 공고, 모든 입찰(confidence와 reason),
모든 낙찰, 파싱 실패한 원문이 들어 있다.

## 세는 방식

한 태스크당:

```
messages = 3 (공고: contractor 수만큼)
         + 입찰로 잡힌 개수
         + 1 (낙찰. 입찰이 하나도 없으면 없음)
```

- **파싱 실패**는 "입찰하지 않음"으로 친다. `parse_fails`로 따로 센다.
- 세 명 모두 입찰하지 않으면 `unassigned`.
- 낙찰은 confidence 최고. **동점이면 먼저 답한 쪽**(A → B → C 순).
- `correct + misawards + unassigned == tasks` 는 `run_round()`에서 assert로 건다.

## 제출 전 검증

```bash
cd ../../..
python scripts/check_week03.py submissions/25622005/week-03
```

`results.csv`(조건당 3줄 이상)와 `logs/`(9개 이상)는 실행 후에 생기므로, 실행
전에는 그 두 항목이 FAIL로 나오는 게 정상이다.

PR 제목: `[week-03] 25622005`

## 주의

- `tasks.json`의 `gold`는 **첫 실행 전에 커밋**한다. 결과를 보고 고치면 실험이 아니다.
- 파싱 실패와 크래시는 지우지 말고 센다. 둘 다 발견이다.
- 조건 간에 달라지는 것은 skill 문자열과 C의 프롬프트 한 문장뿐이어야 한다.
