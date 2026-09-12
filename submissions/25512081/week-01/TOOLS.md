clock tool의 추가했고 description으로
"Return the current date and time. Use this only when the task needs to know what time or date it is right now. It takes no arguments and does no arithmetic or file access."
으로 작성하였음. 우선 기존의 read_file, calculator tool만 존재했을때, 모델이 자체적으로 가능한 합산의 경우는 calculator 호출없이 잘 답하지만,해당 툴도 없고 모델이 잘 답할 수 없는 부분에 해당하는 현재시각을 지어내어 대답하는 hallucination을 발견하여 현재시각을 답하는 도구 clock을 추가하였다. 도구의 특징 상 입력을 정확히 정의하고, 도구 호출 시점을 명확히 하기 위해, return the current date and time 이후의 두문장을 추가하여 description을 완성하였다.
