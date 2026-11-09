# TOOLS.md — 왜 clock을 이렇게 설명했는가

새 도구는 `clock`이다. 첫 초안의 설명은 `"Get the current time."` 한 줄이었다. 코드에는 이 초안을 `CLOCK_DESC=terse`로 남겨 두었고, 실제로 채택한 설명은 `CLOCK_DESC=full`(기본값)이다. 설명을 길게 바꾼 이유는 세 가지다. 첫째, 모델은 자기가 오늘 날짜를 모른다는 사실을 잘 모른다. 학습 데이터 기준의 날짜를 자신 있게 써 버리는 일이 흔하기 때문에, 설명에 "Do not guess the date from memory; you do not know today's date without this tool"을 명시해 도구를 **부르지 않을 때의 위험**을 알려 줬다. 둘째, 도구가 무엇을 반환하는지(`YYYY-MM-DD HH:MM:SS`, timezone, 요일)를 적어 두면 모델이 결과를 다시 파싱하는 단계를 줄이고, 날짜 차이를 `calculator`로 넘길 때 형식을 예측할 수 있다. 셋째, "언제 부르라"는 트리거 조건("today's date, the current time, or how much time remains until/since some date")을 적었다. 도구가 2개일 때는 `read_file`과 `calculator`의 역할이 서로 겹치지 않아 선택이 쉬웠지만, 3개가 되면 모델은 "이 태스크에 시간이 관련되는가"를 판단해야 하므로 판단 기준을 설명 안에 넣어 주는 편이 맞다고 봤다. 반대로 timezone 파라미터는 `required`에서 빼고 기본값(Asia/Seoul)을 두었다. 모델이 매번 timezone을 지어내다가 잘못된 값을 넣어 실패하는 경로를 없애기 위해서다. 잘못된 timezone이 들어오더라도 예외를 던지지 않고 "IANA 이름을 쓰라"는 에러 문자열을 돌려주어 모델이 스스로 고칠 수 있게 했다.

## 실험 설계 (도구가 하나 늘었을 때 선택 행동 관찰)

같은 goal을 다음 세 설정으로 실행하고 `logs/`에 남긴다.

| 로그 | 설정 | 보려는 것 |
|---|---|---|
| `run-01-two-tools.txt` | `DROP_CLOCK=1` (calculator, read_file만 전송) | 날짜를 모를 때 모델이 추측하는지, 모른다고 답하는지 |
| `run-02-terse.txt` | `CLOCK_DESC=terse` | 한 줄 설명으로도 clock을 호출하는지, 호출 순서는 어떤지 |
| `run-03-full.txt` | 기본값 (`CLOCK_DESC=full`) | 설명이 호출 여부·순서·불필요한 호출에 영향을 주는지 |
| `run-04-sum-only.txt` | 기본값, goal은 starter의 "sum the numbers" | 시간이 필요 없는 태스크에서 clock을 쓸데없이 부르는지 |

## 관찰 결과 (2026-09-07 실행, `claude-sonnet-4-5`, `bash run_experiments.sh`)

1. **도구 2개 (`run-01-two-tools.txt`)**: read_file → calculator 2회(병렬) → 종료. 날짜는 "오늘 날짜를 알 수 없다"고 밝히고, 메모 날짜(9/1)를 오늘로 가정하면 29일이라는 조건부 답을 낸 뒤 사용자에게 되물었다. 날짜를 지어내 단정하지는 않았지만 태스크를 끝내지 못했다. clock이 없을 때 이 모델의 기본 행동은 "추측"이 아니라 "질문"이었다.
2. **terse 설명 (`run-02-terse.txt`)**: 1단계에서 read_file과 clock을 **병렬로** 호출했다. 그런데 `clock({'timezone': 'UTC'})`로 timezone을 스스로 채워 넣었다. 설명에 기본값이 없으니 모델이 가장 무난한 값을 지어낸 것이다. UTC 기준으로 9월 7일이 되어 남은 일수는 23일.
3. **full 설명 (`run-03-full.txt`)**: 같은 병렬 호출이지만 `clock({})`으로 인자를 비웠고, 기본값 Asia/Seoul이 적용되어 9월 8일 03:16 KST가 나왔다. 남은 일수 22일. run-02와 약 10초 차이로 실행했는데 **최종 답이 하루 다르다**. 설명 한 문장("Default timezone is Asia/Seoul")이 인자 선택을 바꾸고, 그 인자가 답을 바꿨다. 이것이 terse 설명을 버리고 full을 채택한 직접적 근거다.
4. **시간이 필요 없는 goal (`run-04-sum-only.txt`)**: clock을 부르지 않았다. 도구가 하나 늘어도 불필요한 호출은 없었다. 대신 "sum the numbers"를 문자 그대로 받아 Attendees 4까지 합쳐 82004를 냈다. 이는 도구 선택이 아니라 goal 문구의 문제다.
5. **공통**: 세 실행 모두 단계 수는 3으로 같았고 호출 순서의 혼란은 없었다. 금액 계산은 calculator에 맡겼지만 날짜 차이(9/30 − 9/8)는 도구 없이 머릿속으로 계산했다. 도구가 있어도 모델은 자기가 쉽다고 판단한 계산은 직접 한다. 도구 설명이 길어질수록 1단계 입력 토큰이 늘었다(666 → 741 → 813).
6. **검증되지 않은 것**: full 설명의 "Do not guess the date from memory" 문구가 효과가 있었는지는 이 실험으로는 알 수 없다. run-01에서 도구 없이도 모델이 날짜를 지어내지 않았기 때문이다. 이 문구는 다른 모델이나 더 긴 대화에서만 차이를 낼 수 있다.

**버린 것**: terse 설명 `"Get the current time."`. 코드에는 `CLOCK_DESC=terse`로 남겨 두어 누구나 같은 비교를 재현할 수 있다.
