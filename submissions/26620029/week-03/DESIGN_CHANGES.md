# Week 03 — 후속 설계 변경 (gold 격리 · 입찰당 토큰 계측 · run 폴더 분리)

`REPORT.md`에 담긴 최초 제출물은 그대로 CI 채점 대상(`results.csv`, `logs/`,
`tasks.json`, `REPORT.md`)으로 남아 있다. 이 문서는 그 이후에 추가한 세
가지 구조 변경을 설명한다: (1) gold를 에이전트에게 구조적으로 격리, (2)
공고 기반 입찰 시 소비 토큰을 입찰 단위로 계측, (3) 앞으로의 시도를
`runs/` 폴더로 분리 보관.

## 1. gold 격리: "빼는 코드"가 아니라 "가질 수 없는 자료구조"

기존에도 `ANNOUNCE_TEMPLATE`은 `task["id"]`와 `task["desc"]`만 채워
넣었고 `gold`는 낙찰 이후 채점에만 쓰였다 — 프롬프트에 값이 새는 일은
없었다. 다만 `run_task(task, ...)`가 `gold`를 포함한 전체 task dict를
그대로 받고 있었기 때문에, 나중에 누군가 디버깅 로그에 `task` 전체를
찍거나 프롬프트에 "추가 컨텍스트"를 붙이는 식으로 고치면 `gold`가 함께
새어 나갈 수 있는 구조였다. 즉 "지금은 안 새지만, 왜 안 새는지는 코드를
꼼꼼히 읽어야만 알 수 있는" 상태.

바꾼 것 (`contract_net.py`):

- `public_view(task)`가 `{"id", "desc"}`만 골라낸 dict를 만든다. `gold`는
  이 dict에 애초에 존재하지 않는다.
- `run_task(announcement_task, gold, profiles, meter, log)`로 시그니처를
  바꿔, 공고문 생성 경로(`announcement_task`)와 채점 전용 값(`gold`)을
  별도 인자로 분리했다. 프롬프트를 만드는 코드는 `gold`라는 이름의
  변수를 아예 스코프에 갖지 않는다.
- `assert "gold" not in announcement_task` — 회귀 방지용 안전장치.
  나중에 실수로 원본 task dict를 통째로 넘기면 프롬프트를 만들기 전에
  바로 죽는다 (조용히 새는 대신 시끄럽게 실패).
- `run_condition`이 `public_view(task)`와 `task["gold"]`를 호출부에서
  미리 분리해서 넘긴다. gold는 `run_task` 안에서 낙찰이 이미 결정된
  *다음* 줄에서만 등장한다.

이 정도로는 "gold 노출"을 완전히 증명하지는 못하지만(진짜 보장하려면
공고문 문자열 자체를 스캔하는 테스트가 필요하다), 최소한 "gold가 담긴
자료구조가 프롬프트 생성 함수의 인자로 물리적으로 존재하지 않는다"는
더 강한 성질로 바뀌었다.

## 2. 입찰당 토큰 계측

기존 `Meter`는 run 전체의 누적 토큰(`meter.tokens`)만 로그 맨 끝에
찍었다 — "이 조건, 이 run이 총 몇 토큰 썼는가"는 알 수 있어도 "task-3의
공고문을 읽고 `casey`가 입찰하는 데 토큰이 얼마나 들었는가"는 알 수
없었다. `overconfident`/`homogeneous` 조건이 배정 품질만 깨뜨리는지,
아니면 입찰 자체를 (예: 더 장황한 reason으로) 더 비싸게 만드는지 구분할
수 없다는 뜻이다.

바꾼 것:

- `model.py`의 `Meter`가 `last_input`/`last_output`을 추가로 들고
  있는다 — `add()`가 호출될 때마다 그 한 번의 호출의 input/output
  토큰을 남긴다 (누적치 `tokens`는 그대로 유지, 기존 사용처를 깨지
  않는다).
- `run_task`가 `call_model()` 호출 직후 `meter.last_input` /
  `meter.last_output`을 읽어 그 입찰 하나의 토큰 비용을 `bid` dict에
  기록한다.
- 각 (task, contractor) 입찰마다 레코드를 하나씩 모아
  (`task_id, contractor, bid, confidence, input_tokens, output_tokens,
  total_tokens`) `run_condition`이 `(totals, bid_records)` 튜플로
  반환한다.
- `run_experiments.py`가 이 레코드를 `bids.csv`에 쓴다. `results.csv`의
  헤더는 CI 계약(`scripts/check_week03.py`)이 고정하고 있어 열을 추가할
  수 없으므로, 토큰 데이터는 별도 파일로 낸다.

이러면 예를 들어 "`homogeneous`에서 세 계약자가 모두 85로 동점 입찰한
task-2가, 서로 다른 스킬을 가진 `baseline`의 같은 task-2보다 입찰 토큰이
더 드는가"처럼, REPORT.md의 해석 섹션이 지금은 답할 수 없는 질문에
`bids.csv`만으로 답할 수 있다.

## 3. `runs/` 폴더 — 시도마다 분리 보관

기존 `run_experiments.py`는 실행할 때마다 루트의 `results.csv`와
`logs/`를 덮어썼다 (REPORT.md 1절에도 명시). 이건 "채점용 산출물 한
벌만 있으면 된다"는 최초 과제 요구사항에는 맞지만, 이후에 프로토콜을
바꿔가며 여러 번 실험할 때는 이전 시도가 다음 시도로 덮어써져 비교가
안 된다는 문제가 있다.

바꾼 것:

- `--update-root` 플래그가 없으면 매 실행이
  `runs/<타임스탬프>[-<label>]/` 새 폴더를 만들고, 그 실행의
  `results.csv`, `bids.csv`, `logs/*.txt`, `config.json`(provider,
  model, temperature, `--label`, 실행에 쓴 `tasks.json`의 sha256 앞
  12자리)을 그 폴더 안에만 쓴다. 다른 시도의 파일을 절대 건드리지
  않는다.
- `--update-root`를 주면 기존 동작 그대로 루트의 `results.csv`/`logs/`를
  갱신한다 — CI와 `REPORT.md`가 가리키는 채점용 산출물을 재생성할 때만
  이 플래그를 쓴다.
- `tasks.json`의 해시를 `config.json`에 남기는 이유: task 문구를 손보고
  다시 실험했을 때, 어떤 `runs/` 폴더가 어떤 버전의 task set으로 나온
  결과인지 나중에도 구분하기 위함이다.

사용 예:

```bash
# 새 실험 (기본): runs/20260917T140500-blind-gold-v2/ 에 결과 저장
python run_experiments.py --runs 3 --label blind-gold-v2

# 채점용 루트 산출물 재생성 (REPORT.md와 짝이 맞아야 할 때만)
python run_experiments.py --runs 3 --update-root
```

## 실행 안내

이 세션에는 `ANTHROPIC_API_KEY`도 `OPENAI_API_KEY`도 설정돼 있지 않아,
위 변경을 실제 모델 호출로 재현하지는 못했다. 대신 `call_model`을
가짜 함수로 바꿔치기한 드라이런으로 다음을 확인했다:

- `run_condition`이 만든 프롬프트 문자열 어디에도 `"gold"`라는 토큰이
  없다는 것 (있었다면 드라이런의 `assert`가 바로 실패했을 것).
- `bid_records`에 입찰마다 `input_tokens`/`output_tokens`/`total_tokens`가
  채워진다는 것.
- `run_experiments.py --label ...`이 `runs/<타임스탬프>-라벨/`에
  `config.json`, `results.csv`, `bids.csv`, `logs/`를 정확히 만든다는 것.

키가 있는 환경에서 `python run_experiments.py --runs 3 --label <실험명>`을
실행하면 실제 `bids.csv`가 나온다. `python scripts/check_week03.py
submissions/26620029/week-03`는 루트 산출물만 보므로 이번 변경으로도
계속 통과한다 (확인 완료).
