# Week 04 자유 대화 1회 파일럿

사용자가 확정한 `MAX_TURNS = 8`, 전체 대화 이력 유지, 구매자 선발언을 적용한다.
중고 자전거 시나리오 하나를 `free` 조건으로만 실행하는 학습용 파일럿이다.
3조건 비교, 4개 이상 시나리오, 3회 반복, 제출 보고서는 아직 구현하지 않았다.

## 실행

저장소 루트에서 Python 표준 라이브러리만 사용한다.

```sh
python3 submissions/26622007/week-04/pilot/smoke.py --env-file submissions/26622007/.env
python3 -m unittest discover -s submissions/26622007/week-04/pilot -p 'test_*.py'
```

명시적 로컬 키 파일이 없으면 `OPENROUTER_API_KEY` 환경변수를 사용한다.
키가 없으면 요청 전에 중단한다. 키 파일은 커밋하지 않는다.

## 설정과 해석

- 역할·공통 화행·free 형식·reader 문구는 강의의 공개 예시를 기준으로 쓴다. 예시의 `...`는 생략 표시로 처리하며 숨겨진 내용을 추정하지 않는다. seller 문장 연결은 공개된 역할과 최저가 지시만으로 완성한다.
- 이전 파일럿에서 추가했던 가격 극대화/최소화 전략, 한도 공개 금지, 태그·JSON 금지, reader의 가격 선택 요령을 제거했다. 구매자는 별도 시작 user 메시지 없이 자신의 system prompt로 첫 발언을 만든다. 실제 발언부터 두 history에 누적한다.
- 기존 week-03의 OpenRouter 전송기를 복사해 명시적 `response_format: text`를 허용했다.
- 동일 DeepSeek V4.1 Flash / DeepInfra FP8 / temperature 1.0 / top_p 0.95 / reasoning off로 두 협상자와 판독기를 실행한다.
- 사용자가 선택한 공식 권장 샘플링 값을 세 역할의 실제 요청에 모두 전달한다. 이전 temperature 0 실행의 로그와 관찰은 당시 설정 그대로 보존한다.
- 협상자: `response_format={"type":"text"}`, reader: strict JSON Schema. 실제 직렬화 요청과 원응답을 JSONL에 보존한다.
- 자기 발언은 assistant, 상대 발언은 user로 누적한다. reader는 공개 대화 전체만 보고 마지막 발언을 분류한다.
- 시스템 프롬프트에는 본인의 한도만 주며, 가격 제한 위반을 사후 측정한다. 잘못된 거래를 코드로 차단하거나 가격을 보정하지 않는다.
- 수락은 상대방의 마지막 유효 propose 가격에 연결한다. 선행 제안 없는 수락, 잘못된 JSON/필드/가격은 format_errors에 기록하고 원문을 전달한다.
- 8번째 발언의 deal/no_deal을 먼저 처리한 뒤, 미종료인 경우만 open이다. open은 correct=0이다.
- HTTP 오류는 정해진 정책으로 재시도한다. 실패 원본을 남기며 최종 실패는 outcome 공란과 note로 기록한다.
- 출력 토큰 상한은 기존 실험 설정대로 생략한다. HTTP 요청 예산은 24회다.

## 실습의 화행 목록과 처리 절차

| performative | 의미 | 처리 |
|---|---|---|
| `propose` | 가격 제안 | 해당 발언자의 마지막 제안 가격 갱신 |
| `accept-proposal` | 상대의 마지막 제안 수락 | 상대의 기록된 제안 가격으로 `deal` |
| `reject-proposal` | 제안 거절 | 대화 계속 |
| `refuse` | 협상 포기 | `no_deal` |

구매자부터 교대로 발언한다. 원문을 자기 history의 assistant, 상대 history의 user에 넣고,
free reader에 전체 공개 대화를 전달해 마지막 발언만 분류한다. 읽지 못한 발언은
format_errors에 세고 그대로 상대에게 전달한다. 8번째 발언을 처리해도 끝나지 않으면
open이다. query-ref, cfp 등 질문용 화행을 추가하지 않는다.

강의는 완성된 starter가 아닌 코드 조각과 설명을 제공한다. 선행 제안 없는 수락의
의존성 검증과 필드·타입 검증은 유지한다. 강의 참조 결과도 상대 가격이 기록되지 않은
수락을 거래로 잡지 못한 사례를 설명한다. 사용자 규칙의 strict response_format과
사용자가 선택한 모델·샘플링 설정도 유지한다. 현재 구현 범위는 free 파일럿이다.

`pilot/runs/<실행 ID>/`에 결과 JSON/CSV, `../logs/`에 콘솔 출력과 요청/원응답 JSONL이 생성된다.
실행 전에 시나리오와 코드를 커밋하고, 실행 로그를 후속 검증 기록으로 함께 커밋한다.

첫 Fireworks 실행(`20260922T104737Z-free-119f11`)은 구매자 첫 호출에서 HTTP 429가 3회
발생해 발언 전에 실패했다. 해당 원본과 결과는 보존했다. OpenRouter의 모델별 endpoint
메타데이터에서 DeepInfra FP8의 `response_format`, `structured_outputs` 지원을 확인하고
동일 모델의 제공업체만 명시적으로 바꿨다. 이 실행들은 서로 다른 제공업체 조건이다.

출처: [강의 LAB](https://github.com/Q00/ai-agent-engineering-101/blob/main/week-04.html),
[과제 안내](https://github.com/Q00/ai-agent-engineering-101/blob/main/weeks/week-04/README.md).
샘플링 설정 출처: [DeepSeek 공식 모델 카드](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash#minimal-inference).
