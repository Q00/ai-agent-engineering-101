# TOOLS.md

`write_note`의 description은 `"Append text to a file in the working directory."`로 작성했다. `Write` 대신 `Append`를 사용한 이유는 기존 내용을 덮어쓰는 것이 아니라 파일 뒤에 내용을 추가하는 도구라는 점을 모델에게 명확하게 전달하기 위해서다. 또한 기존 `read_file`의 `"Read a text file in the working directory."`와 문장 구조를 비슷하게 맞춰, 두 도구의 역할을 쉽게 비교하고 구분할 수 있도록 했다. 실제로 세 번의 실행에서 `read_file`과 `write_note`를 혼동하는 경우는 없었다(`logs/run-01-no-write.txt`, `logs/run-02-three-tools.txt`, `logs/run-03-denied-path.txt`).

도구를 하나 추가했다고 해서 모델이 `write_note`를 불필요하게 호출하지는 않았다. 쓰기 작업을 요구하지 않은 첫 번째 실행에서는 `write_note`가 등록되어 있었음에도 호출하지 않았고(`logs/run-01-no-write.txt`), 쓰기가 필요한 작업에서만 해당 도구를 선택했다. 이를 통해 description이 각 도구의 역할을 구분하고 필요한 상황에서만 선택하도록 하는 데 충분히 기능했다고 볼 수 있었다.

description은 가능한 한 짧게 유지하되, 모델이 언제 이 도구를 선택해야 하는지는 알 수 있도록 작성했다. 개행을 자동으로 추가하거나 파일이 없으면 새로 생성하는 동작처럼 도구 선택에 직접 영향을 주지 않는 구현 세부사항은 일부러 포함하지 않았다.

한편 `"in the working directory"`라고 명시했음에도 모델이 작업 폴더 밖의 경로를 시도한 경우가 있었다(`logs/run-03-denied-path.txt`). 이때는 description 자체보다 `denied: path outside the working directory`라는 실행 결과가 다음 행동을 수정하는 데 더 직접적으로 작용했다. 따라서 description에 모든 예외 상황을 길게 설명하기보다는 도구의 기본 역할과 사용 범위를 간결하게 제시하고, 실행 중 발생하는 문제는 tool result를 통해 피드백하는 방식이 더 적절하다고 판단했다.
