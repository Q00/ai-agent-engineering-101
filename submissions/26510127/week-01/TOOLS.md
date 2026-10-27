\[추가한 것]

write\_note를 통해서 피자+콜라 2개 세트 구성이 있을 때 한 세트 당 할인 받은 금액이 얼마인지 계산한 것을 저장하도록 했다.



\[터미널에서의 실행 방법]

$env:OPENAI\_API\_KEY="Openrouter API 키 사용"

$env:OPENAI\_BASE\_URL="https://openrouter.ai/api/v1"

$env:AGENT\_MODEL="google/gemma-4-26b-a4b-it:free"

python first\_agent.py "notes.txt를 읽고, 세트 하나당 할인 금액을 계산해서 result.txt에 '세트당 할인액: N원' 형식으로 저장해줘."

