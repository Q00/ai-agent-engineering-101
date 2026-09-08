# TOOLS.md

## clock

`clock`의 description은 "Return the current date and time. Optionally pass an IANA timezone name (e.g. 'Asia/Seoul', 'UTC') to get the time in that zone instead of local time."로 작성함. 처음에는 text 형태의 `location`(예: "서울")을 받는 방식도 고려했지만, 이 경우 도구 쪽에서 도시명 --> 타임존 매핑 테이블을 별도로 구현해야 하고, 동명 도시가 여러 국가에 존재하는 등 모호한 지명을 처리하기 어렵다는 문제가 있었음. 반면 모델은 이미 "서울은 Asia/Seoul"이라는 지식을 갖고 있으므로, 자연어 요청을 IANA 표준 타임존 이름으로 변환하는 작업은 모델에게 맡기고 도구는 표준화된 입력만 받도록 설계함. description에 IANA 이름 형식과 예시(`'Asia/Seoul'`, `'UTC'`)를 명시한 것도 모델이 자유 형식 문자열 대신 정확한 타임존 식별자를 채워 넣도록 유도하기 위해 작성 하였음. 또한 `timezone`을 `required`에 넣지 않고 선택 인자로 둔 이유로는 사용자가 특정 지역을 언급하지 않은 "지금 몇 시야?" 같은 요청에서도 로컬 시간을 바로 출력할 수 있도록 하기 위함이었음.

실제, `calculator`, `read_file`, `clock` 세 개의 도구를 동시에 활용한 case("지금 서울 시간 알려주고, notes.txt를 읽어서 숫자를 합산해줘")를 실행하였을 때, `clock(timezone="Asia/Seoul")` → `read_file(path="notes.txt")` → `calculator(expression=...)` 순서로 스스로 필요한 도구를 골라 순차 호출함. 이는 도구가 잘 구현되었음을 보여줌.