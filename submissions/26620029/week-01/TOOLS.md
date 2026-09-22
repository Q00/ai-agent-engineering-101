# TOOLS.md

## 추가한 도구: write_note

`write_note(content: str)`는 현재 시각을 파일명으로 사용해(`YYYYMMDD_HHMMSS.txt`)
메모나 계산 결과를 작업 폴더에 저장하는 도구다. description을
"Save a note, summary, or calculation result to a timestamped text file"로
작성해, 모델이 계산 결과를 저장할 때 자연스럽게 이 도구를 선택하도록 유도했다.

도구가 2개(calculator, read_file)에서 3개로 늘어나자, 모델은 "notes.txt를
읽고 합산한 뒤 저장해줘" 같은 요청에서 read_file → calculator → write_note
순서로 스스로 체이닝했다. 


이번 실습에서

1. notes.txt를 note.txt로 요청 문장에 잘못 적으면
(예: note.txt vs notes.txt) read_file이 FileNotFoundError로 프로그램
전체를 종료시켜버리는 취약점도 함께 발견했다 — 에러 처리가 없는 도구는
루프 전체를 깨뜨릴 수 있다는 걸 확인했다.

2. "테스트" 등 단순한 내용으로 입력시 오류가 발생했음.


3. 그 외 문장을
"notes.txt를 읽고, 과일의 단위가 개  인 과일명을 찾아서 수량과 함께 메모에  저장해줘" 
  [tool] read_file({'path': 'notes.txt'}) -> 사과 3개
바나나 12개
포도 25개
딸기 50알
  [tool] write_note({'content': "단위가 '개'인 과일 목록:\n- 사과: 3개\n- 바나나: 12개\n- 포도: 25개"}) -> saved to 20260905_110240.txt
완료했습니다! notes.txt에서 단위가 "개"인 과일들을 찾아 메모에 저장했습니다.


과일의 단위가 '개' 나 '알'을 구분하여 처리하는 것을 확인하였음.


