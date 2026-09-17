# TOOLS.md — 세 번째 도구 `clock`의 설명을 이렇게 쓴 이유

`clock`의 설명은 "Get the current date and time (Asia/Seoul, ISO 8601). Always call this
when the current date or time is needed; never guess it. Takes no arguments."로 작성하였습니다.

이유는 크게 3가지가 있습니다.
- 첫째, **시간대와 형식(Asia/Seoul, ISO 8601)을 설명에 작성한 것**은 반환값의 해석을 모델에게 맡기지 않기 위해서입니다.
입력 파일(notes.txt)의 날짜도 ISO 형식이라, 모델이 두 값을 같은 형식으로 놓고 비교하게 됩니다.

- 둘째, **"never guess it"**은 모델이 현재 시각을 착각하고 지어내는 경향을 막기 위한 지시로 작성하였습니다.
설명 단계에서 "추측 금지"를 도구 사용 조건으로 명시했습니다.

- 셋째, **"Takes no arguments"**는 기존 두 도구는 모두 인자가 있어, 모델이 인자를 지어내는 상황이 발생하면 TypeError가 나올 수도 있기 때문에
추가를 하였습니다.

## 실제 실행 결과

실제 실행(logs/run-01.txt)에서 모델은 빈 인자 `{}`를 정확히 보낸 것을 확인했습니다.

모델은 read_file → clock → calculator 순으로 필요한 도구만 한 번씩 불렀고, 날짜 차이 계산("10일 전", "39일 남음")은 calculator를 쓰지 않고 스스로 수행하였습니다.
calculator의 설명이 "산술 수식 계산"으로 정의되어 있어 날짜 연산에는 사용하지 않은 것으로 생각하였습니다.

또한, 예산 계산에서는 notes.txt의 `3,400,000` 표기를 쉼표 없는 `3400000 - 850000`으로 정규화해 넘겼습니다.