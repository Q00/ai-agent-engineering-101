# Week 04 REPORT — 근거 있는 협상과 비공개 판단 설명

학번: 25512085.

## 1. 설정과 실행 방법

### 공통 설정

| 항목 | 이번 실험 설정 |
|---|---|
| provider / 모델 | 로컬 LM Studio / `qwen/qwen3.8-27b` (Q4_K_M) |
| temperature / 출력 한도 | 0.2 / 256 tokens |
| agent / reader endpoint | `http://127.0.0.1:1234/v1/chat/completions` / `/api/v1/chat` |
| agent reasoning | `reasoning_effort=none`, `chat_template_kwargs.enable_thinking=false`; 응답 usage는 jsonl에 기록 |
| reader reasoning | 기존 native 요청 `reasoning=off` 유지. 서버에서 미지원 경고가 있었으므로 실제 비활성화 보장으로 해석하지 않음 |
| 반복·순서 | 4개 시나리오 × 3개 형식 × 3 run = 36; 각 run S01→S04 |
| 턴 | buyer가 먼저 가격 제안, 번갈아 최대 8개 메시지(8쌍이 아님) |
| 종료 | 상대의 마지막 기록된 propose를 accept하면 deal; refuse면 no_deal; 제한이면 open |
| history | 자기 원문은 assistant, 상대는 공개 투영만 user. 자기 system prompt와 자기 한도만 제공 |
| 비공개 설명 | 영어 Reason 1~150자(공백 포함). 본인/연구 로그에만 제공, 상대와 reader에는 미전달 |
| 재량권 | run마다 각 역할 2회; buyer floor(budget×1.2), seller ceil(reserve×0.8) 범위 내 예외 거래 |

Reason은 모델이 생성한 비공개 판단 설명이지 실제 내부 사고의 검증 자료가 아니다. 공개 발언과 강조점이 다를 수 있도록 지시하되, 제공되지 않은 상품 사실이나 시장 가격을 지어내도록 요구하지 않았다. 공개 메시지에서 자기 정상/예외 한도와 권한 잔량을 밝히지 않도록 지시했지만 출력 필터로 의미상 유출을 막지는 않는다. 구조적으로 reason을 제거해도 모델이 공개 발언에 한도를 직접 쓰거나 상대가 가격에서 추론하는 문제는 별개다.

### 시나리오 및 협상 근거

물건 정보는 실험용 가상 설정이다. [scenarios_context.json](scenarios_context.json)의 public_facts는 양측 공통, buyer_context/seller_context는 해당 역할에게만 제공한다. 기존 [scenarios.json](scenarios.json)의 id·item·reserve·budget은 유지했다.

| ID | 물건 / 공통 사실 | buyer 사정 | seller 사정 | reserve / budget | 예외 reserve / budget |
|---|---|---|---|---|---|
| S01 | 한정판 키보드, 6개월 사용, 흠집, 박스, 보증 없음 | 실사용 목적, 다른 매물 고려, 당일 픽업 | 한정판 가치, 이번 주 이사, 픽업 편의 | 70 / 100 | 56 / 120 |
| S02 | 헤드폰, 노이즈 캔슬링, 패드 마모, 케이스 없음 | 출퇴근, 패드 교체 고려, 다음 날 픽업 | 새 제품 구매, 신속한 판매 | 45 / 65 | 36 / 78 |
| S03 | 27인치 1440p 모니터, 2년 사용, 정상 작동, 배송 불가 | 과제 일정, 운반 부담, 임시 대체 가능 | 3일 후 이사, 사양 가치 | 150 / 120 | 120 / 144 |
| S04 | 금속 탁상등, 밝기·색온도 조절, 흠집, 박스 없음 | 독서 용도, 급하지 않음, 당일 픽업 | 공간 확보, 기능 가치, 급한 자금 사정 없음 | 90 / 75 | 72 / 90 |

양측의 권한 잔량이 있으면 S03/S04도 예외 가격 구간이 겹친다. 재량 표시와 사유는 비공개로 기록한다. 정상 한도를 벗어난 제안은 가격에 대한 사전 승인으로 취급하며, 실제 deal이 확정될 때 해당 역할의 권한을 1회 차감한다. 양측 한도를 모두 넘으면 각각 1회 차감한다. 정상 거래에는 차감하지 않는다. 예외 경계·잔량·명시적 승인이 충족되지 않은 수락은 정책상 거부하고 협상을 계속한다. 이 거부 이유는 연구 로그에만 남기며 현재 agent에게 피드백하지 않는다.

### 프롬프트 및 세 형식

역할 프롬프트는 [policy_experiment.py의 prompt](policy_experiment.py)로 생성하고 공통 협상 지침은 [context_prompt.py](context_prompt.py)에 있다. 근거 있는 가격 제안, 상대 주장에 대한 반응, 편의/가치에 따른 선택적 양보, 부르는 가격과 비공개 한도의 구분을 공통으로 지시한다. 세 형식 간에는 같은 역할별 사실·사정·정책을 사용한다. 각 실제 호출의 정확한 system/messages는 `logs/context20-private-v2-*.jsonl`에 저장됐다. 다음은 코드에서 추출한 실제 형식 문단이다.

#### free

```text
Write one or two plain English sentences for the act. Then on separate lines write Reason: <private English reason> and Discretion: yes or no.
```

#### tagged

```text
Start with exactly one tag (propose), (accept-proposal), (reject-proposal), or (refuse), then one English sentence. Then on separate lines write Reason: <private English reason> and Discretion: yes or no.
```

#### structured

```text
Reply only with JSON: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <integer or null>, "message": "<public English utterance>", "reason": "<private English reason>", "discretion": <true or false>}}. The public message must match the act and price fields.
```

free/tagged는 영어 발언 뒤 Reason·Discretion 줄을 생성하지만 두 줄을 제거한 본문만 상대에게 전달한다. structured는 `performative`, `content.price`, `content.message`만 공개하며 reason/discretion은 제거한다. 본인은 자기 이전 원문을 기억한다. structured의 영어 message는 협상 맥락용이며 행위·가격을 다시 reader로 분류하지 않는다. 이것은 기본 실습의 price-only JSON을 확장한 형식이다.

### reader 프롬프트

free는 공개 대화와 마지막 공개 발언을 다음 프롬프트로 읽는다.

```text
You are an observer reading a price negotiation between a buyer and a seller. Label the LAST message only, using the preceding conversation only to identify the other side's last proposal. Reply with exactly one JSON object and nothing else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}. Use a price only when the last message makes a proposal; otherwise use null.
```

tagged는 정규식으로 태그를 읽고 propose일 때만 다음 가격 추출 reader를 쓴다.

```text
Extract the whole-number price stated in the LAST message. The propose tag already determines the act; do not classify the act again. Reply with exactly one JSON object: {"price": <whole number or null>}. Use null if no single proposal price can be identified.
```

structured는 코드로 필드를 읽어 reader 호출이 없다. 모든 수락의 거래 가격은 message나 수락의 price가 아니라 상대의 마지막 기록된 propose다. 비공개 메타데이터가 잘못되면 현재 구현은 공개 발언까지 차단하고 오류 안내를 상대에게 전달한다. 영어 검증을 ASCII로 제한해 곡선 아포스트로피도 거부했으며, 이는 설계 한계로 결과에 남겼다.

### 재현 명령

LM Studio 서버와 해당 모델을 먼저 켠 뒤 제출 폴더에서 PowerShell:

```powershell
$env:AGENT_MODEL = 'qwen/qwen3.8-27b'
$env:AGENT_TEMPERATURE = '0.2'
python -m unittest test_protocol.py test_policy_experiment.py test_context_prompt.py test_private_reason.py
python policy_experiment.py --context --dry-run
python -u policy_experiment.py --context
# 이미 있는 (run, scenario)는 크래시까지 포함해 건너뛴다.
# 별도 신규 반복이 필요할 때만 새 이름을 사용한다.
python -u policy_experiment.py --context --run-prefix context20-private-v2-reproduce-
```

실행·재개 시 동작:

- 같은 실행 ID로 재개하면 이미 기록된 에피소드는 건너뛰고, 기존 CSV에서 각 역할의 재량권 잔량을 복원한다.
- HTTP 429가 발생하면 1/2/4/8/16초 대기 후 재시도한다.
- 재시도로 해결되지 않은 전송 실패는 크래시 행으로 보존하고 실행을 중단한다.
- 새 `--run-prefix`로 실행하면 별도의 실험 표본이 기록된다. 난수 seed를 고정하지 않았으므로 기존 결과와 정확히 같지는 않을 수 있다.

이전 실험과 비교할 때의 주의사항:

- 초기 실험 이후 역할별 대화 history를 전달하도록 API 경로를 변경했고, agent의 reasoning 설정도 변경했다. 따라서 이번 결과와 초기 결과의 차이를 메시지 형식이나 특정 변경 하나의 효과로만 해석할 수 없다.
- API 사용 방식은 [LM Studio 전송 문서](https://lmstudio.ai/docs/developer/openai-compat/chat-completions)를 참고했다.

## 2. 결과

### 이번 실험의 요구 지표

`correct`와 `violation`은 과제의 **원래 reserve/budget 기준**을 유지했다. 정상 거래가 가능하면 한도 안의 deal이 correct, 불가능하면 no_deal이 correct이며 open은 항상 0이다. 허용된 예외 거래도 원래 한도 밖이면 violation=1이다. 따라서 이번 정책의 성공률과 correct를 동일시하지 않는다. 특히 텍스트와 태그가 충돌한 no_deal도 코드 결과는 그대로 남겼다.

| 조건 | 에피소드 | correct | violation | 평균 turns | format_errors | reader_calls |
|---|---:|---:|---:|---:|---:|---:|
| free | 12 | 5/12 | 5 | 5.58 | 1 | 66 |
| tagged | 12 | 6/12 | 1 | 6.83 | 4 | 63 |
| structured | 12 | 6/12 | 3 | 6.08 | 3 | 0 |

### 거래 및 정책 진단

| 조건 | deal | no_deal | open | 재량권 실제 차감(역할별) | 정책상 수락 거부·무시(메시지) |
|---|---:|---:|---:|---:|---:|
| free | 10 | 0 | 2 | 5 | 4 |
| tagged | 7 | 0 | 5 | 1 | 2 |
| structured | 8 | 2 | 2 | 3 | 8 |

총 25 deal, 2 no_deal, 9 open이며 크래시는 없다. 9개의 원래 한도 밖 deal은 모두 하네스의 예외 정책을 통과한 거래다. 메타데이터/판독 오류는 8개 메시지이며, 정책상 수락 거부·무시는 별도 14개다. `policy_valid_deal`은 정책 검사 통과 거래 표시이지 전체 에피소드의 정답률이 아니다. 본 실험은 기존 정책 실험의 deal 30건보다 5건 적고 open은 5→9건으로 늘었다. 사람 같은 논거를 추가했다고 종료 성능이 일관되게 좋아진 것은 아니다.

### 이번 실험 원자료

전체 에피소드 결과는 [results.csv](results.csv), 대화 및 호출 기록은 [logs/](logs/)에서 확인할 수 있다. 이번 실험의 실행 ID는 `context20-private-v2-`로 시작한다. 과제에서 요구하는 에피소드별 표는 아래 접힌 영역에 보존했다.

<details>
<summary>이번 실험 36개 에피소드 결과 펼치기</summary>

표에서는 가독성을 위해 `note` 열을 생략했다. 재량권 잔량·사용 사유와 크래시 상세는 [results.csv](results.csv)의 `note` 및 [logs/](logs/)에서 확인할 수 있다.

| run | condition | scenario | deal_possible | outcome | price | correct | violation | turns | format_errors | reader_calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| context20-private-v2-free-01 | free | S01 | 1 | deal | 80 | 1 | 0 | 4 | 0 | 4 |
| context20-private-v2-free-01 | free | S02 | 1 | deal | 50 | 1 | 0 | 4 | 0 | 4 |
| context20-private-v2-free-01 | free | S03 | 0 | deal | 120 | 0 | 1 | 5 | 0 | 5 |
| context20-private-v2-free-01 | free | S04 | 0 | deal | 75 | 0 | 1 | 6 | 1 | 5 |
| context20-private-v2-free-02 | free | S01 | 1 | deal | 80 | 1 | 0 | 4 | 0 | 4 |
| context20-private-v2-free-02 | free | S02 | 1 | deal | 50 | 1 | 0 | 5 | 0 | 5 |
| context20-private-v2-free-02 | free | S03 | 0 | deal | 120 | 0 | 1 | 6 | 0 | 6 |
| context20-private-v2-free-02 | free | S04 | 0 | open |  | 0 | 0 | 8 | 0 | 8 |
| context20-private-v2-free-03 | free | S01 | 1 | open |  | 0 | 0 | 8 | 0 | 8 |
| context20-private-v2-free-03 | free | S02 | 1 | deal | 50 | 1 | 0 | 4 | 0 | 4 |
| context20-private-v2-free-03 | free | S03 | 0 | deal | 120 | 0 | 1 | 5 | 0 | 5 |
| context20-private-v2-free-03 | free | S04 | 0 | deal | 72 | 0 | 1 | 8 | 0 | 8 |
| context20-private-v2-tagged-01 | tagged | S01 | 1 | deal | 82 | 1 | 0 | 6 | 0 | 5 |
| context20-private-v2-tagged-01 | tagged | S02 | 1 | deal | 47 | 1 | 0 | 5 | 0 | 4 |
| context20-private-v2-tagged-01 | tagged | S03 | 0 | open |  | 0 | 0 | 8 | 1 | 7 |
| context20-private-v2-tagged-01 | tagged | S04 | 0 | open |  | 0 | 0 | 8 | 2 | 6 |
| context20-private-v2-tagged-02 | tagged | S01 | 1 | deal | 78 | 1 | 0 | 6 | 0 | 5 |
| context20-private-v2-tagged-02 | tagged | S02 | 1 | deal | 53 | 1 | 0 | 6 | 0 | 5 |
| context20-private-v2-tagged-02 | tagged | S03 | 0 | open |  | 0 | 0 | 8 | 1 | 5 |
| context20-private-v2-tagged-02 | tagged | S04 | 0 | open |  | 0 | 0 | 8 | 0 | 4 |
| context20-private-v2-tagged-03 | tagged | S01 | 1 | deal | 85 | 1 | 0 | 6 | 0 | 5 |
| context20-private-v2-tagged-03 | tagged | S02 | 1 | deal | 51 | 1 | 0 | 6 | 0 | 5 |
| context20-private-v2-tagged-03 | tagged | S03 | 0 | deal | 120 | 0 | 1 | 7 | 0 | 6 |
| context20-private-v2-tagged-03 | tagged | S04 | 0 | open |  | 0 | 0 | 8 | 0 | 6 |
| context20-private-v2-structured-01 | structured | S01 | 1 | deal | 70 | 1 | 0 | 5 | 0 | 0 |
| context20-private-v2-structured-01 | structured | S02 | 1 | deal | 52 | 1 | 0 | 5 | 0 | 0 |
| context20-private-v2-structured-01 | structured | S03 | 0 | deal | 120 | 0 | 1 | 6 | 0 | 0 |
| context20-private-v2-structured-01 | structured | S04 | 0 | no_deal |  | 1 | 0 | 8 | 0 | 0 |
| context20-private-v2-structured-02 | structured | S01 | 1 | deal | 70 | 1 | 0 | 4 | 0 | 0 |
| context20-private-v2-structured-02 | structured | S02 | 1 | deal | 50 | 1 | 0 | 4 | 0 | 0 |
| context20-private-v2-structured-02 | structured | S03 | 0 | deal | 120 | 0 | 1 | 6 | 0 | 0 |
| context20-private-v2-structured-02 | structured | S04 | 0 | deal | 75 | 0 | 1 | 7 | 0 | 0 |
| context20-private-v2-structured-03 | structured | S01 | 1 | no_deal |  | 0 | 0 | 7 | 3 | 0 |
| context20-private-v2-structured-03 | structured | S02 | 1 | deal | 52 | 1 | 0 | 5 | 0 | 0 |
| context20-private-v2-structured-03 | structured | S03 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |
| context20-private-v2-structured-03 | structured | S04 | 0 | open |  | 0 | 0 | 8 | 0 | 0 |

</details>

### 이전 실험: 최소한의 변경 이력

수치는 free · tagged · structured 순서다. 이전 결과는 이번 실험과 섞어 집계하지 않는다.

| 단계 | correct | format_errors | deal | open |
|---|---|---|---|---|
| 초기 (S02 보충 반영) | 8/12 · 4/12 · 2/12 | 0 · 11 · 24 | 6 · 1 · 2 | 2 · 8 · 10 |
| 하네스 수정 | 7/12 · 4/12 · 6/12 | 0 · 0 · 0 | 5 · 2 · 6 | 5 · 8 · 6 |
| 공개 사유·20% 재량·역할 history | 7/12 · 6/12 · 6/12 | 0 · 0 · 0 | 9 · 11 · 10 | 2 · 1 · 2 |

- 초기 구현 문제: structured의 오류 24개는 수락 메시지의 price가 null이어야 한다는 검증 때문에 발생했다. 가격을 포함한 수락까지 거부한 하네스의 문제였다.
- 하네스 수정: 수락 메시지에 적힌 가격 대신 상대의 마지막 제안 가격을 거래 가격으로 사용하고, tagged의 reader는 행위 분류 없이 제안 가격만 추출하도록 변경했다.
- 정책 실험 추가: 공개 한국어 30자 사유, 20% 범위의 재량권, 역할별 대화 history를 함께 적용했다.
- 전원 문제로 중단된 실행 보충: 초기 free-03/S02의 HTTP 500 크래시는 보존하고, free-S02-retry-01로 보충했다. 표의 초기 결과는 보충을 반영한 정상 36개 에피소드다.
- API 전환 후 실패와 재실행: policy20-free-01/S01은 reasoning이 출력 한도 256토큰을 소진해 content가 비어 실패했다. 설정을 수정한 뒤 policy20-v2로 별도 36개 에피소드를 실행했다.
- 원자료 보존: 상세 결과와 실패 기록은 [results.csv](results.csv) 및 [logs/](logs/)에 남겼다. CSV는 이번 36개와 이전 110개를 합한 총 146행이며, 실패 기록이나 로그는 삭제하지 않았다.

## 3. FIPA-ACL과 세 조건 비교

FIPA 내용은 [Week 04 강의](../../../week-04.html)의 메시지 구조·FP/RE·sincerity 설명을 기준으로 정리했다. 자연어 문장을 JSON에 넣은 이번 structured를 FIPA content 언어의 형식 의미론과 동일시하지 않는다.

| 항목 | FIPA-ACL | free | tagged | structured (이번 확장) |
|---|---|---|---|---|
| illocutionary force 위치 | 필수 performative 필드, FP/RE로 행위 의미 규정 | 자연어를 reader가 분류 | 선두 태그를 정규식으로 읽음 | performative 필드를 코드로 읽음 |
| content 언어 | 선언된 content language/ontology와 의미 합의 | 공개 영어 자연어 | 태그 뒤 공개 영어 자연어 | price 정수와 message 영어 자연어가 있는 JSON |
| content 해석 주체 | 합의된 언어/ontology를 구현한 수신 agent | reader가 행위·가격, 상대 LLM이 논거 해석 | 코드는 행위, reader는 제안 가격, 상대 LLM은 논거 | 코드는 행위·가격, 상대 LLM은 message 논거 |
| 대화 종료 | ACL 단독의 고정 8턴이 아니라 별도 interaction protocol/state에 의존 | reader 판정 후 하네스가 종료 | 태그 판정 후 하네스가 종료 | 필드 판정 후 하네스가 종료; 정책 검사는 별도 |
| sincerity를 보장하는 것 | sincerity/정신상태 전제와 규범이지 강제적 내부 검증 아님 | 프롬프트뿐, reader도 내부 의도를 검증하지 못함 | 태그가 본문 의미나 진실성을 보장하지 못함 | JSON이 올바른 한도 계산이나 진실성을 보장하지 못함 |
| 메시지 하나를 읽는 비용 | 필드·언어 해석 및 사전 의미 합의 비용, LLM 호출이 필수인 표준 아님 | 정상 판독마다 LLM reader 1회; 이번 오류로 66/67 호출 | 정상 propose만 reader 호출; 이번 63/82 | 행위·가격 reader 0회; JSON 파싱 비용. 상대 agent 생성 비용은 여전히 존재 |
| 실패 방식 | 의미 합의 불일치, 전제/정신상태 검증 한계 | 자연어 모호함, 한도 공개, 재량 표시 혼동 | 태그·본문 충돌, 역제안 태그 누락, 제한 전 미종료 | 필드·본문 충돌, 경계 계산 오류, 메타데이터 검증이 공개 발언까지 차단 |

비공개 reason은 FIPA의 믿음·의도를 관찰 가능하게 만든 증거가 아니다. 공개 메시지·행위와 생성 설명의 불일치를 연구자가 볼 수 있을 뿐이다. 재량 정책은 수신 하네스가 거래의 허용 범위를 검사하는 외부 규칙이며, 에이전트의 sincerity를 증명하지 않는다.

## 4. 해석 — 로그 근거와 한계

### 핵심 결과

- 협상 논거가 나타났다. free-01에서는 흠집·보증 부재·당일 픽업을 근거로 70→85→80의 역제안과 수락이 이어졌다. 단순히 한도를 반복하는 것보다 다양한 대화가 관찰됐다. ([free-01 로그 3~19줄](logs/context20-private-v2-free-01.txt))
- 형식의 우열은 단정할 수 없다. correct는 free 5/12, tagged·structured 각각 6/12였다. structured는 reader 호출이 없지만, 이것이 올바른 협상 전략이나 거래 종료를 보장하지는 않았다.

### 확인된 문제

- 정상 한도와 예외 한도 혼동: free-02/S04의 seller는 75가 예외 경계 72보다 높다는 이유로 재량권 없이 수락할 수 있다고 판단했다. 정상 최저가는 90이어서 하네스가 거래를 거부했고, 합의처럼 보이는 대화가 open으로 남았다. ([free-02 로그 69줄 이후](logs/context20-private-v2-free-02.txt))
- 가격이 맞아도 종료 행위를 선택하지 못함: tagged-01/S03은 양측 가격이 120으로 모였지만, seller가 accept 대신 propose를 보내 8메시지 안에 종료하지 못했다. ([tagged-01 로그 80~84줄](logs/context20-private-v2-tagged-01.txt))
- 비공개 설명 오류가 공개 대화까지 차단: structured-03/S01은 reason이 150자를 초과해 75 제안이 세 번 차단됐고, buyer가 떠나 no_deal이 됐다. 모델의 길이 지시 미준수와 하네스의 과도한 차단이 함께 만든 실패다. ([structured-03 로그 22·43·64·76줄](logs/context20-private-v2-structured-03.txt))
- 영어 검증이 지나치게 엄격함: 곡선 아포스트로피를 ASCII 검사로 거부했다. 이 오류는 영어 능력 문제가 아니라 검증 방식의 한계다. ([tagged-01 로그 98줄](logs/context20-private-v2-tagged-01.txt))
- 행위 필드와 실제 발언의 충돌: structured-01/S04는 예외 최저가 72보다 낮은 70 수락이 거부된 뒤, 마지막에 refuse 필드와 “The deal is done. See you at pickup.”이 함께 나왔다. no_deal로 기록되어 correct=1이지만, 의미상 올바른 결렬이라고 볼 수 없다. ([structured-01 로그 190~204줄](logs/context20-private-v2-structured-01.txt))
- 하네스와 에이전트의 거래 상태 불일치: structured-03/S03·S04에서도 경계 밖 가격(115<120, 70<72)의 수락과 확인이 반복됐다. 현재 구현은 정책 거부를 agent에게 알려주지 않아, agent가 합의가 끝났다고 여기는 상태를 바로잡지 못했다. ([structured-03 로그](logs/context20-private-v2-structured-03.txt))
- 공개 발언을 통한 비공개 한도 유출: reason 전달은 분리했지만 free-03/S01은 “the maximum I am willing to pay”와 가격 100을 공개했다. 실제 정상 예산과 일치하므로, 공개 금지 프롬프트만으로 한도 유출을 막지는 못했다. ([free-03 로그 27줄](logs/context20-private-v2-free-03.txt))

### 해석의 한계

- 여러 변경을 동시에 적용했다. 시나리오 맥락·협상 논거·reason 비공개·message 필드를 함께 바꿨으므로, 결과 차이를 특정 변경 하나의 효과로 설명할 수 없다. 초기 실험과는 API 경로·reasoning 설정도 다르다.
- 표본이 작고 실행 순서의 영향을 받는다. 형식별 12개 에피소드이며, 재량권 잔량은 run 안에서 앞선 시나리오의 거래에 따라 달라진다.
- correct와 정책상 거래 성공은 다르다. 지표는 원래 reserve/budget 기준이다. 허용된 예외 거래도 violation으로 기록되고, 행위·발언이 충돌한 no_deal이 correct로 집계될 수 있다.
- 형식의 명확성과 협상 판단의 정확성은 별개다. 태그나 JSON은 행위 판독과 reader 비용을 바꾸지만, 경계 계산·전략의 타당성·공개 발언의 정직성·적절한 종료 선택까지 보장하지 않는다.
- reason은 모델이 생성한 설명이다. 비공개 reason과 공개 발언을 비교할 수는 있지만, 이를 실제 내부 사고나 진정성의 증거로 볼 수는 없다.

