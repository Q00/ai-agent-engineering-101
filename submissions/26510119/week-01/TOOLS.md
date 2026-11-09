# TOOLS.md

## 추가한 도구: write_note

`write_note`의 description을 `"Save text content to a file in the working directory."`로 작성했다.
"Save"라는 동사를 사용한 이유는, 모델이 결과를 파일로 저장해야 하는 상황에서 이 도구를 선택하도록 유도하기 위해서다.
"text content"를 명시한 이유는, 이 도구가 어떤 텍스트든 받아서 저장할 수 있다는 점을 모델에게 알려주기 위함이다.
"in the working directory"를 붙인 이유는 `read_file`과 동일한 보안 제약이 있음을 description 수준에서 드러내기 위해서다.

## 관찰: 프롬프트의 구체성이 도구 사용에 미치는 영향

도구를 추가한 뒤, 프롬프트를 얼마나 구체적으로 작성하느냐에 따라 모델의 도구 활용이 크게 달라졌다.
"save the result to memo.txt"처럼 간단하게 지시하면 모델은 최종 숫자(`69504`)만 저장했고, 계산 과정은 포함하지 않았다.
반면 "show each number and the full calculation step by step, then save the detailed breakdown and final result to memo3.txt"처럼 구체적으로 지시하면 모델이 항목별 숫자와 단계별 계산 과정을 포함하여 저장했다.
이를 통해, description은 모델이 도구를 "선택"하게 만들지만, 도구를 "어떻게" 사용하는지는 프롬프트의 구체성에 달려 있다는 점을 확인할 수 있었다.
