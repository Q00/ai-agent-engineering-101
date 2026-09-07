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

## 관찰 결과

(실행 후 기록. 아래 "실행 로그" 항목의 명령으로 재현 가능.)
