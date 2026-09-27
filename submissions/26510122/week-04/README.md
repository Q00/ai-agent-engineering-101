# Week 04 — Message-format negotiation

Buyer와 Seller가 같은 네 시나리오를 `free`, `tagged`, `structured` 메시지 형식으로 협상한다. 역할 prompt, 모델, 턴 한도는 고정하고 system prompt의 마지막 형식 문단과 protocol reader만 조건별로 바꾼다.

## 실행

제출 결과는 Codex CLI의 `gpt-5.6-luna`로 생성했다. Codex CLI는 temperature를 노출하지 않으므로 설정하지 않았다.

```bash
python3 -m unittest discover -p 'test_*.py' -v
python3 run.py --backend codex-cli --model gpt-5.6-luna \
  --run-prefix luna- --all --runs 3
```

OpenRouter를 사용할 수도 있다. 이 경우 `.env` 또는 환경변수에 키를 두고 직접 커밋하지 않는다.

```bash
OPENROUTER_API_KEY=... python3 run.py --backend openrouter --all --runs 3
```

실행기는 `results.csv`에 이미 존재하는 `(run, scenario)`를 건너뛰므로 중단된 실행을 같은 명령으로 이어갈 수 있다. 실패한 에피소드도 빈 측정값과 오류 이름을 기록하고 원본 JSONL 로그를 보존한다.

## 선택 실험: 논쟁적 수사

필수 실험과 분리해 Seller에게만 쇼펜하우어의 논쟁적 변증술에서 가져온 다섯 수사 전략을 지시했다. Buyer, 시나리오, 모델, 턴 한도와 protocol reader는 바꾸지 않았다. 이 처치는 상대를 설득하는 수사가 협상 메시지 형식과 만날 때 어떤 현상이 생기는지 살펴보기 위한 탐색 실험이며, 필수 실험의 `results.csv`에는 섞지 않았다.

```bash
python3 run.py --backend codex-cli --model gpt-5.6-luna \
  --seller-strategy eristic --all --runs 1
```

free와 tagged에서는 Seller가 가격을 `fair`, 상대 제안을 `weak` 또는 `undervalued`라고 규정하는 발화가 나타났다. structured는 스키마에 수사를 담을 필드가 없으므로 행위와 가격만 출력했다. 세 형식 모두 4개 중 1개만 correct였고 violation은 없었다. 한 번만 반복한 탐색 결과이므로 전략의 일반적인 효과로 해석하지 않는다.

## 파일

- `scenarios.json`: 비공개 reserve와 budget을 가진 네 협상 상황
- `protocol.py`: 세 메시지 형식, reader, 정답·violation 판정
- `run.py`: Buyer/Seller 호출, 대화 상태, 로그와 결과 저장
- `eristic.py`: Seller 전용 논쟁적 수사 처치 prompt
- `results.csv`: 본 실험 36개와 보존한 초기 Sol 시도 6개
- `logs/`: 조건·반복별 원본 실행 로그
- `eristic_results.csv`, `eristic_logs/`: 선택 실험 결과와 원본 로그
- `smoke/`: backend와 형식을 확인한 초기 실행 기록
- `REPORT.md`: 설정, 결과, FIPA-ACL 비교, 로그 기반 해석
- `test_*.py`: 모델 호출 없는 parser와 episode 테스트
