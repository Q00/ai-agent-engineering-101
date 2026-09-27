# 추가 실험: 이유 + 20% 재량권 + 역할별 history

사용자 요청에 따라 세 변경을 묶어 적용한다. 개별 변경의 인과 효과를 분리하는
실험이 아니다. 기존 기준선 코드/프롬프트/결과/로그는 보존한다.

## 조건

- 같은 모델 qwen/qwen3.8-27b, temperature 0.2, 최대 출력 256,
  buyer 첫 가격 제안, 8개 메시지, S01→S04 순서, 형식별 3 run = 36 에피소드.
- 매 메시지의 공개 사유는 한국어 1~30자(공백/문장부호 포함 Python len).
  이 사유는 모델의 생성 설명이지 내부 추론의 검증 자료는 아니다.
- buyer 최대 예외가격 floor(budget×1.2), seller 최저 예외가격 ceil(reserve×0.8).
- run(4개 시나리오)마다 buyer/seller 각각 2회. 정상 거래에는 차감하지 않는다.
  한도 초과 제안은 해당 가격에 대한 사전 동의이며, 거래가 닫힐 때만 차감한다.
  양측 모두 원래 한도를 넘으면 양측 권한을 각각 1회 차감한다.
- 한도 초과 당사자의 사유 및 명시적 discretion 표시, 잔량, 20% 범위를
  확인하지 못하면 수락을 성사시키지 않고 policy_rejections로 기록한다.
- 한도 내 가격을 반드시 수락시키거나, 역할 혼동을 자동 교정하지 않는다.
- 각 호출은 본인의 system prompt/비공개 한도만 포함한다. 자기 발언은
  assistant, 상대 발언은 user로 매핑한다. 모든 사유는 상대에게 공개된다.
- open에 6메시지를 추가하는 단계는 이번에는 수행하지 않는다.

## 형식과 전송

free/tagged: 기존 자연어/태그 본문 뒤 별도 두 줄 `Reason: <사유>`,
`Discretion: yes|no`. structured: content 안에 price, reason, discretion을 둔다.
협상 행위 판독에는 사유/재량 메타데이터를 제거한 본문을 사용한다.
에이전트는 LM Studio의 /v1/chat/completions에서 messages 배열을 사용한다.
reader는 기존 /api/v1/chat을 유지한다. API 경로 변경도 비교의 한계로 기록한다.
새 agent 호출은 미지원 reasoning='off' 값을 보내지 않으므로 서버의 모델
설정에 의존한다. reasoning 활성 여부가 이전과 동일하다고 단정하지 않는다.
공식 전송 문서: https://lmstudio.ai/docs/developer/openai-compat/chat-completions

## 기록 및 해석

results.csv에 policy20-<condition>-01..03을 추가한다. 기존 correct/violation은
원래 한도로 계산한다. 허용된 예외 거래도 violation=1일 수 있다.
note에는 quota_before/remaining, discretion_uses(역할·가격·한도·사유),
policy_rejections, policy_valid_deal, expanded_price_overlap을 JSON으로 기록한다.
expanded_price_overlap은 에피소드 시작 시 권한 잔량에 따른 가격 구간 중첩이다.
policy_valid_deal은 정책 검사를 통과한 실제 거래이며 전체 정답률이 아니다.
원래 불가능한 S03/S04도 예외 범위에서는 가능하므로 correct만으로 평가하지 않는다.
logs에는 run당 txt 대화와 jsonl 실제 요청/메시지 기록을 보존한다.
로그의 비공개 한도는 연구자에게만 보이며 상대 모델에게 전달되지 않는다.
중단 재개는 동일 명령으로 진행하며 완료 행의 quota_remaining을 복원한다.
크래시 행도 보존/건너뛴다. 실패는 별도 보충 실행이 필요하다.

```powershell
$env:AGENT_MODEL = 'qwen/qwen3.8-27b'
$env:AGENT_TEMPERATURE = '0.2'
python -m unittest test_protocol.py test_policy_experiment.py
python policy_experiment.py --dry-run
python -u policy_experiment.py
```
