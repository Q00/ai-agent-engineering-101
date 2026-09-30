# Week 04 - buyer/seller 메시지 형식 비교

buyer와 seller가 번갈아 가격을 협상한다. 같은 시나리오와 역할 프롬프트를
free / tagged / structured 형식으로 각각 3회 실행한다. 기본 구성은
4개 시나리오 × 3조건 × 3회 = 36개 에피소드, 최대 8메시지다.

buyer/seller가 협상 당사자이고 reader는 공개 대화의 마지막 메시지를
해석하는 보조 모델 호출이다. 세 역할은 같은 모델과 sampling 설정을 쓴다.
도구 호출은 사용하지 않는다.

| 파일 | 역할 |
|---|---|
| scenarios.json | 실행 전에 고정한 상품과 비공개 한도 |
| prompts.py | ROLE + COMMON + FORMAT 및 reader prompt |
| model_client.py | OpenAI 호출, 명시적 재시도, usage 계측 |
| protocol.py | 자연어 reader, 맨 앞 태그, JSON 파서 |
| negotiation.py | 개별 history와 교대 협상 상태 기계 |
| run_experiment.py | 에피소드별 CSV append, 원본 로그, resume |
| test_week04.py | API 없는 회귀 테스트 |
| analyze_results.py | 로그에서 결과를 독립 재계산하고 CSV와 대조 |
| experiment.json | 실행 코드·프롬프트·시나리오·설정의 fingerprint |
| REPORT.md | 설정, 집계/전체 결과, FIPA 비교, 로그 기반 해석 |

## 설치 및 검증

Python 3.12, macOS 또는 Linux를 사용한다. runner의 파일 잠금은
fcntl을 사용하므로 Windows에서는 WSL 같은 Linux 환경이 필요하다.

```bash
cd submissions/26510124/week-04
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest -v test_week04
```

OPENAI_API_KEY를 환경변수로 설정한다. 이미 설정했다면 다시 입력할 필요 없다.
키를 파일이나 명령 기록에 저장하지 않으려면 터미널의 비표시 입력을 사용할 수 있다.
프로그램은 .env를 읽지 않는다. OpenAI 공식 API 실험에서는
OPENAI_BASE_URL을 설정하지 않는다.

## 실제 실험과 resume

```bash
.venv/bin/python run_experiment.py --runs 3 --max-turns 8 --model gpt-5.4-mini --temperature 0.2 --reasoning-effort none
```

반복 1의 free → tagged → structured, 반복 2와 3의 같은 순서로 실행한다.
각 조건/반복에서 시나리오 파일 순서를 따른다. 완료 즉시 CSV에 한 행을
append/flush/fsync한다. 원본 메시지, reader 원문, 파서 결과, 상태 변화와
API 재시도를 logs/의 조건·반복별 JSONL 파일에 콘솔 출력 그대로 기록한다.

같은 명령을 다시 실행하면 (condition, run, scenario)가 이미 기록된
완료·실패 행을 건너뛴다. 로그에는 결과가 있지만 CSV 기록 전에 중단됐다면
로그로 행을 복구한다. 시작만 남은 에피소드는 중단 행으로 보존한다.
완료·실패 기록이 모두 존재하면 이 명령이 추가 API 호출을 하지 않는다.
서로 다른 설정이나 실행 코드를 같은 결과에 섞으려 하면 거절한다.

제출 결과를 보존하며 독립 재현하려면 별도 출력 경로를 사용한다.

```bash
.venv/bin/python run_experiment.py --output-dir reproduced --runs 3 --max-turns 8 --model gpt-5.4-mini --temperature 0.2 --reasoning-effort none
.venv/bin/python analyze_results.py --root reproduced
```

통신 오류·429·일부 5xx에는 1/2/4/8초의 제한된 재시도를 적용한다.
SDK 자체 재시도는 꺼서 호출을 중복 계측하지 않는다. reader_calls는
실제 reader 요청 시도 수이며 재시도와 최종 실패도 포함한다.
agent_calls 및 retries는 note에 따로 기록한다. 재시도 중 usage를 알 수
없으면 전체 토큰 합계를 unknown으로 두며 마지막 성공 응답의 usage만
전체 비용이라고 쓰지 않는다.
backoff 도중 강제 중단되면 예정된 재시도가 실제 전송됐는지 불명확할 수 있다.
분석기는 이 경우 미확인 호출 수를 unknown으로 다룬다.

## 해석 규칙

- 상대의 비공개 한도는 해당 agent의 system prompt에 주입하지 않는다.
  reader도 공개 transcript만 본다. agent가 스스로 한도를 발화하면 그것은
  공개 transcript에 포함된다.
- 입력이 잘못되어도 원문은 양쪽 history에 전달된다. 해석 실패만 센다.
- accept-proposal은 상대의 마지막 유효 propose 가격을 수락한다.
  accept에 적힌 새 숫자는 거래 가격을 바꾸지 않는다.
- 상대 제안 없는 accept는 거래를 만들지 않으며 semantic_errors로 센다.
- reject-proposal 뒤의 자연어 역제안은 가격 상태를 갱신하지 않는다.
- 한도 위반은 거래를 차단하는 조건이 아니라, 거래 후 측정하는 지표다.
- 가격은 0 이상의 정수다. bool/float/음수/숫자 문자열은 거절한다.
  JSON 중복 키와 NaN/Infinity도 거절한다.
- free reader는 응답 전체가 JSON이어야 한다. tagged의 performative는
  맨 앞 태그가 결정하고 reader의 price만 사용한다.
- structured는 선행 공백 뒤 첫 JSON 객체를 읽고 후행 텍스트를 기록하되
  의미 해석에 쓰지 않는다. Markdown fence나 문장 안 JSON을 발굴하지 않는다.
  필수 키와 타입을 검사하고 추가 키는 무시한다.
- 비제안 행위의 정수 price는 타입만 검사한 뒤 무시하며 null도 허용한다.

## 결과 확인

```bash
.venv/bin/python analyze_results.py
python3 ../../../scripts/check_week04.py .
```

analyze_results.py는 협상 실행 함수를 재사용하지 않고 로그의 행위와
가격으로 거래를 다시 계산한다. 공식 CI는 형식만 검사하므로 두 검사를
함께 실행한다. 모든 실험 결과와 오류는 REPORT.md의 한계와 함께 해석한다.
