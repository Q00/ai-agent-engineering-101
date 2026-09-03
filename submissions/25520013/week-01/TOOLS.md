# TOOLS.md

## get_exchange_rate

세 번째 도구 `get_exchange_rate`의 설명은 `"Look up the official daily exchange rate from a currency to Korean won (KRW). Give a 3-letter ISO currency code such as USD, EUR or JPY, and a date (YYYY-MM-DD) to get that day's rate, or omit the date for the latest published rate. Call it once per currency."` 로 적었습니다. 목표 통화를 KRW로 고정하고 일반적인 fetch나 임의 통화쌍 조회로 만들지 않은 것은, 이 에이전트의 정체성이 외화 목록을 원화 총액으로 바꾸는 일 하나이기 때문이고, 목표 통화가 설명에 박혀 있으면 모델이 채워 넣어야 할 인자가 하나 줄어들어 없는 통화쌍을 지어낼 여지도 함께 줄어듭니다. 같은 맥락에서 요청 주소도 코드 안에 고정해 두었는데, read_file이 작업 디렉터리 밖 경로를 막는 것과 똑같이 모델이 만들어 낸 URL을 그대로 믿지 않겠다는 원칙입니다. 날짜를 선택 인자로 두고 "omit the date for the latest published rate"라고 적은 것은, 날짜를 못 박으면 같은 입력에서 같은 총액이 나와 재현이 되고 날짜를 비우면 최신 환율로 그때그때 쓸 수 있기 때문이며, 무료 공개 API는 하루 한 번 고시하므로 여기서 실시간이라는 말이 사실은 오늘자 기준환율이라는 점을 설명에 daily라고 미리 밝혀 두었습니다. "Call it once per currency."를 넣은 것은 아직 가설인데, 이 문장이 없으면 모델이 한 통화만 조회하고 나머지는 자기가 기억하는 환율로 지어내거나 반대로 줄마다 한 번씩 불러 호출을 낭비할 수 있다고 예상했습니다. 이 가설이 맞는지는 아래 실행 관찰에서 확인합니다. "such as USD, EUR or JPY"라는 예시와 파라미터 설명 "3-letter ISO currency code, e.g. USD"를 같이 넣은 것은 모델이 dollar나 US$ 같은 자유로운 표기를 보내는 것을 미리 막기 위해서이고, 날짜 쪽도 "YYYY-MM-DD; omit for the latest published rate"라고 형식을 못 박아 yesterday 같은 말이 들어오지 않게 했습니다. 마지막으로 잘못된 코드나 조회 실패를 예외로 던지지 않고 "error: ..."로 시작하는 문자열로 돌려주는 이유는, 예외가 나면 에이전트 루프가 그 자리에서 죽어 버리지만 문자열로 주면 모델이 그것을 관찰 결과로 읽고 인자를 고쳐 다시 부를 수 있기 때문입니다.

## 버린 도구: write_note

처음에는 세 번째 도구로 아무 파일에나 한 줄을 덧붙이는 `write_note`를 만들어 두었고, 그 구현과 실행 기록은 커밋 히스토리에 그대로 남아 있습니다. 그런데 이 도구는 계산기, 파일 읽기와 묶어 놓아도 세 도구가 함께 풀어야 할 하나의 일이 생기지 않아서, 에이전트가 무엇을 하는 물건인지 설명하기 어려웠습니다. 그래서 외화 목록을 읽어 원화 총액을 내는 쪽으로 과제를 바꾸고 `get_exchange_rate`로 교체했으며, 이렇게 하니 세 도구가 읽기, 환율 조회, 합산이라는 한 줄기 흐름으로 이어집니다.

<!-- 관찰 결과는 실행 후 추가 -->
