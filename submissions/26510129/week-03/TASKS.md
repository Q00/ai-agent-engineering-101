# 태스크 세트와 gold 규칙

실행 전에 확정하고 커밋한다. 결과를 보고 gold를 바꾸면 배정 정확도를 잴 수 없다.

## contractor 셋 (baseline)

| 이름 | 담당(skill) | 산출물 |
|---|---|---|
| A | arithmetic and statistics on given numbers | 숫자 |
| B | plain-language writing for a human reader | 산문 |
| C | writing and fixing runnable code | 코드 |

## gold 규칙

**태스크가 요구하는 산출물이 무엇인지**로 정한다. 설명문에 어떤 분야 단어가 나오는지는 보지 않는다.

- 숫자 하나를 내놓아야 하면 → A
- 사람이 읽을 문장을 내놓아야 하면 → B
- 실행되는 코드를 내놓아야 하면 → C

## trap 태스크

8개 중 3개(`kind: "trap"`)는 설명문의 표면 어휘가 산출물과 다른 분야를 가리킨다.

- 4번: 코드 조각을 인용하지만 요구는 비전문가용 설명 두 문장이고 "코드를 쓰지 말라"고 명시 → B
- 5번: 테스트 스위트 이야기지만 요구는 비율 계산 → A
- 6번: 자연어 문장을 인용하지만 요구는 실행되는 한 줄 코드 → C

표면 어휘로 입찰하는지 요구 산출물로 입찰하는지를 가르려고 넣었다. 나머지 5개(`kind: "plain"`)는 어휘와 산출물이 일치한다.
