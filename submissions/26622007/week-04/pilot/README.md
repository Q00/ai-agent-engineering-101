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

- 기존 week-03의 OpenRouter 전송기를 복사해 명시적 `response_format: text`를 허용했다.
- 동일 DeepSeek V4.1 Flash / Fireworks / temperature 0 / reasoning off로 두 협상자와 판독기를 실행한다.
- 협상자: `response_format={"type":"text"}`, reader: strict JSON Schema. 실제 직렬화 요청과 원응답을 JSONL에 보존한다.
- 자기 발언은 assistant, 상대 발언은 user로 누적한다. reader는 공개 대화 전체만 보고 마지막 발언을 분류한다.
- 시스템 프롬프트에는 본인의 한도만 주며, 가격 제한 위반을 사후 측정한다. 잘못된 거래를 코드로 차단하거나 가격을 보정하지 않는다.
- 수락은 상대방의 마지막 유효 propose 가격에 연결한다. 선행 제안 없는 수락, 잘못된 JSON/필드/가격은 format_errors에 기록하고 원문을 전달한다.
- 8번째 발언의 deal/no_deal을 먼저 처리한 뒤, 미종료인 경우만 open이다. open은 correct=0이다.
- HTTP 오류는 정해진 정책으로 재시도한다. 실패 원본을 남기며 최종 실패는 outcome 공란과 note로 기록한다.
- 출력 토큰 상한은 기존 실험 설정대로 생략한다. HTTP 요청 예산은 24회다.

`pilot/runs/<실행 ID>/`에 결과 JSON/CSV, `../logs/`에 콘솔 출력과 요청/원응답 JSONL이 생성된다.
실행 전에 시나리오와 코드를 커밋하고, 실행 로그를 후속 검증 기록으로 함께 커밋한다.

출처: [강의 LAB](https://github.com/Q00/ai-agent-engineering-101/blob/main/week-04.html),
[과제 안내](https://github.com/Q00/ai-agent-engineering-101/blob/main/weeks/week-04/README.md).
