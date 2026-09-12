# Tool Design

이번 과제에서는 세 번째 도구로 `write_note`를 추가하였다.

`write_note`는 사용자가 계산이나 파일 분석 결과를 별도의 파일에 저장하거나 기록하도록 요청했을 때 사용하도록 설계하였다.

Tool description은 다음과 같이 작성하였다.

"Write text to a file when the user asks to save or record a result."

단순히 "파일에 쓴다"라고만 설명하면 모델이 어떤 상황에서 이 도구를 사용해야 하는지 불명확할 수 있기 때문에, 사용자가 결과를 저장하거나 기록하도록 요청했을 때 사용한다는 조건을 description에 포함하였다.

최종 실험에서는 Agent가 `read_file → calculator → write_note` 순서로 도구를 선택하여 `notes.txt`의 내용을 읽고, 합계를 계산한 뒤, 결과를 `result.txt`에 저장하였다.