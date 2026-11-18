# 판독 하네스 수정 및 재실행

기존 results.csv의 37행과 기존 logs는 수정하지 않는다. 새 실험은
`corrected-free-01`부터 `corrected-structured-03`까지 36행을 같은 CSV에
추가한다. 분석에서는 기존 실험과 corrected 실험을 섞어 집계하지 않는다.

## 변경한 것

- structured: propose에는 정수 가격이 필요하다. 다른 행위의 price는
  거래 가격 결정에 사용하지 않으며, 숫자가 있다는 이유로 수락을 거부하지 않는다.
- tagged: 정규식으로 얻은 행위가 권위 있는 값이다. propose의 가격만
  전용 reader가 추출하며, 행위를 다시 분류하지 않는다. 따라서 태그와
  본문이 충돌해도 이를 자동으로 수락으로 고치지 않는다.
- runner: --run-prefix로 과거 실행과 다른 ID를 사용한다.

## 유지한 것과 한계

모델 qwen/qwen3.8-27b, temperature 0.2, 최대 출력 256, 4개 시나리오,
각 조건 3회, buyer 첫 가격 제안, 8개 메시지 제한, 역할/형식 프롬프트는 유지한다.
free reader도 변경하지 않는다. 두 판독 변경을 함께 적용하므로 개별
효과를 엄밀히 분리하는 단일 요인 실험이 아니라 하네스 수정 후 재실행이다.

기존 호출은 역할별 assistant/user 메시지 배열 대신 역할 표시가 있는 전체
대화 문자열을 입력한다. 이번에는 비교 조건을 유지하기 위해 변경하지 않았다.
따라서 강의의 history 구현까지 완전히 동일한 재현이라고 주장하지 않는다.
API의 reasoning='off' 요청 또한 서버에서 실제 적용되는지는 별도 확인 대상이다.
수락은 메시지에 적힌 가격이 아니라 상대의 마지막 기록된 propose 가격을 사용한다.

## 실행 및 검증

week-04 폴더에서 PowerShell:

```powershell
$env:AGENT_MODEL = 'qwen/qwen3.8-27b'
$env:AGENT_TEMPERATURE = '0.2'
python -m unittest test_protocol.py
python runner.py --run-prefix corrected- --repeats 3
```

같은 명령을 다시 실행하면 results.csv에 있는 (run, scenario)는 건너뛴다.
크래시 행도 보존하고 건너뛰므로 실패 시 별도 보충 실행으로 기록해야 한다.
기존 과제 REPORT.md는 아직 작성하지 않았다.
