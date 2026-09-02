# TOOLS.md

## write_note

세 번째 도구 `write_note`의 설명은 `"Append one line of text to a file in the working directory, creating the file if it does not exist. Use it to save a result or a memo for later."` 로 적었습니다. 첫 단어를 write가 아니라 append로 시작한 이유는 이 도구가 파일을 덮어쓰지 않고 뒤에 한 줄을 붙이기만 한다는 점을 모델이 가장 먼저 읽게 하기 위해서입니다. 실제 구현도 모드 "a"로만 열기 때문에 입력 파일인 notes.txt나 이미 써 둔 메모가 지워질 수 없고, 모델이 같은 호출을 두 번 반복해도 줄이 하나 더 늘어날 뿐이라 되돌릴 수 없는 사고가 나지 않습니다. "creating the file if it does not exist"를 굳이 붙인 것은, 이 문장이 없으면 모델이 파일이 있는지 확인하려고 read_file을 먼저 호출하고 실패 메시지를 받은 뒤에야 쓰기를 시도하는 낭비가 생길 수 있다고 예상했기 때문입니다. 실제로 그런지는 아래 실행 관찰에서 확인합니다. 용도를 "save a result or a memo for later"라고 좁게 적은 것도 의도적인데, 저장이라는 말이 계산이 끝난 결과를 남기는 행위를 가리키므로 모델이 중간 계산값을 매번 파일에 적는 대신 마지막 단계에서 한 번만 부르도록 유도합니다. 파라미터 설명에 "relative file path, e.g. summary.txt"처럼 예시 파일명을 넣은 것은 모델이 절대경로나 상위 디렉터리 경로를 만들어 내는 것을 미리 막기 위한 장치이고, 구현 쪽에서도 read_file과 똑같이 작업 디렉터리 밖이면 거부하도록 막아 두었습니다. 결국 설명 한 문장에 무엇을 하는지와 언제 부르는지를 같이 넣어야 도구가 인터페이스로 작동한다는 것이 이번 과제에서 확인하려는 부분입니다.

<!-- 관찰 결과는 실행 후 추가 -->
