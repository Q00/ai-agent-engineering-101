# Week 04 — 통신 형식에 따른 협상 비교

성진호 · 25620027

## 1. 설정

실험 완료 후 provider, 모델, temperature, 세 형식 문단, reader 프롬프트와 실행 명령을
기록한다.

## 2. 결과

| condition | correct | violation | mean turns | format errors | reader calls |
|---|---:|---:|---:|---:|---:|
| free | 실행 전 | 실행 전 | 실행 전 | 실행 전 | 실행 전 |
| tagged | 실행 전 | 실행 전 | 실행 전 | 실행 전 | 실행 전 |
| structured | 실행 전 | 실행 전 | 실행 전 | 실행 전 | 실행 전 |

전체 에피소드 표는 실험 완료 후 `results.csv`와 대조해 추가한다.

## 3. FIPA-ACL 비교

| 항목 | FIPA-ACL | free | tagged | structured |
|---|---|---|---|---|
| illocutionary force | performative 필드 | 문맥에서 reader가 추론 | 선두 태그 | JSON performative |
| content 언어 | 선언한 형식 언어·ontology | 자연어 | 자연어 | price 필드 |
| content 해석자 | 합의된 해석기 | LLM reader | 정규식+LLM reader | JSON parser |
| 종료 | 상호작용 프로토콜 | 네 행위·8턴 | 네 행위·8턴 | 네 행위·8턴 |
| sincerity | 규범, 외부 강제 없음 | prompt만 존재 | prompt만 존재 | prompt만 존재 |
| 메시지 읽기 비용 | 결정적 파싱 | 매 메시지 reader | propose만 reader | reader 0회 |
| 예상 실패 | ontology·내부상태 검증 | 행위·가격 오독 | 태그와 문장 불일치 | 형식 위반·정보 손실 |

## 4. 해석

실험 완료 후 어느 조건에서 어떤 수치가 움직였는지 로그의 실제 줄을 인용해 작성한다.
