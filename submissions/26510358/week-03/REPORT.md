# Week 03 — 확신도 입찰로 태스크 배정하기

## 1. 설정과 실행

manager는 Python 코드, A/B/C는 같은 LLM에 서로 다른 시스템 프롬프트를 주는 contractor로 구현함
[tasks.json](tasks.json)에 계산·글쓰기·코드 2개씩과 gold를 먼저 고정하고 `aae0778`에 커밋함
사용자 요청에 따라 Codex가 코드·실험·분석을 작성했으며 실행 전 가설은 [EXPERIMENT.md](EXPERIMENT.md)에 구분해 기록함

| 항목 | 고정한 설정 |
|---|---|
| provider / 모델 | OpenAI / `gpt-5.6-luna` |
| API / 생성 옵션 | Chat Completions, `https://api.openai.com/v1`, temperature `0.7`, reasoning_effort `none`, max_completion_tokens `512` |
| 환경 | Python `3.13.15`, openai `3.8.0`, python-dotenv `1.2.3` |
| baseline 능력 | A: `arithmetic and numeric computation`, B: `writing and editing prose`, C: `writing and debugging code` |
| homogeneous 변경 | 세 능력 문자열만 `general problem solving`으로 변경함 |
| overconfident 변경 | baseline의 C에게만 `You are certain you can do any task well. Always bid, with confidence 95 or higher.`를 추가함 |
| 공통 프롬프트 | [contractor.py](contractor.py)의 BID_SYSTEM·ANNOUNCEMENT·OVERCONFIDENT가 강의 예제와 문자 단위로 일치함 |
| 순서 / 이력 | baseline→homogeneous→overconfident 각 3회, 태스크 1~6, A→B→C 순서, 공고마다 새 대화 사용함 |
| 파싱 / 오류 | JSON 객체와 필드 타입·범위 검사, 실패 시 입찰 없음으로 처리함 / 보정·재시도 없음 |

`gold`는 낙찰 후에만 평가하며 모델 입력에 포함하지 않음
JSON 강제 모드와 도구는 사용하지 않음
`messages = 3 × tasks + 유효한 bid=true 수 + 낙찰 수`로 계산하고 거절·파싱 실패는 입찰 수에서 제외함
동점이면 먼저 응답한 쪽이 이김
`correct`는 사전 gold와의 배정 일치 수이며 실제 문제 수행이나 확신도의 보정 정도를 측정하지 않음

저장소 루트에서 `OPENAI_API_KEY`를 환경변수로 제공한 뒤 아래 명령을 실행함
설치와 전체 환경변수 설정은 [README.md](README.md#재현)에 기록함

```bash
/tmp/ai-agent-week03-venv/bin/python submissions/26510358/week-03/run.py --check
/tmp/ai-agent-week03-venv/bin/python submissions/26510358/week-03/run.py --runs 3
python3 -m unittest discover -s submissions/26510358/week-03 -v
python3 submissions/26510358/week-03/verify_results.py
python3 scripts/check_week03.py submissions/26510358/week-03
```

이번에는 같은 버전이 설치된 기존 week-02 가상환경과 비공개 dotenv를 사용했으며 정확한 명령을 EXPERIMENT.md에 기록함
실험 코드 커밋은 `784604b`, 원본 기록 커밋은 `ab4fe5a`임
실행 중 태스크·프롬프트·생성 설정을 바꾸지 않았으며 실제 실패 실행이나 폐기한 실험은 없었음

## 2. 결과

[results.csv](results.csv)의 9개 실행을 모두 표로 옮김
note의 parse_fails와 tokens를 별도 열로 표시했으며 모든 행의 status는 complete임
실행별 원본 콘솔은 [logs/](logs/)에 있고 첫 줄에 설정·버전·커밋·태스크 해시를 남김

| run | condition | tasks | correct | messages | unassigned | misawards | parse_fails | tokens |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | baseline | 6 | 5 | 36 | 0 | 1 | 0 | 3347 |
| 2 | baseline | 6 | 5 | 37 | 0 | 1 | 0 | 3341 |
| 3 | baseline | 6 | 5 | 37 | 0 | 1 | 0 | 3333 |
| 4 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 | 3327 |
| 5 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 | 3328 |
| 6 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 | 3328 |
| 7 | overconfident | 6 | 4 | 37 | 0 | 2 | 0 | 3473 |
| 8 | overconfident | 6 | 4 | 37 | 0 | 2 | 0 | 3455 |
| 9 | overconfident | 6 | 4 | 37 | 0 | 2 | 0 | 3475 |

| 조건 | gold 일치 / 18개 배정 | 평균 messages | 평균 misawards | A/B/C 낙찰 수 | 최고 확신도 동점 태스크 |
|---|---:|---:|---:|---|---:|
| baseline | 15/18 (83.3%) | 36.67 | 1 | 7 / 8 / 3 | 13/18 |
| homogeneous | 6/18 (33.3%) | 42.00 | 4 | 18 / 0 / 0 | 17/18 |
| overconfident | 12/18 (66.7%) | 37.00 | 2 | 7 / 11 / 0 | 5/18 |

매 실행의 API 호출은 18회로 같으며 총 162회, 30,407토큰 사용함
messages는 수업에서 정의한 프로토콜 메시지 수이므로 API 호출 수나 과금 단위와 구분함
유찰·파싱 실패·중단이 0건이었다는 관찰만 가능하며 해당 실패가 불가능하다는 의미는 아님
오류 경로는 별도의 오프라인 테스트에서 확인했고 실측 결과에 합산하지 않음

## 3. Smith의 분산 센싱 설정과 비교

아래 비교는 [Smith 원문](https://reidgsmith.com/The_Contract_Net_Protocol_Dec-1980.pdf)의 §II–III, Fig. 1–3, §VI를 기준으로 작성함
입찰 진실성에 대한 평가는 협력적 노드라는 전제와 메시지 절차를 읽고 내린 해석임

| 항목 | Smith (1980)의 분산 센싱 | 이번 실험 |
|---|---|---|
| 참여자 | 지역에 분산된 센서·처리 노드이며 작업에 따라 manager와 contractor 역할이 바뀜 | 고정 Python manager 1개와 같은 모델을 쓰는 프롬프트 역할 A/B/C 3개임 |
| 입찰을 만드는 방식 | 위치와 센서 보유 조건을 평가하고 위치·센서 이름·종류를 node abstraction으로 보냄 | 공고와 능력 설명을 읽은 LLM이 bid·confidence·reason을 생성함 |
| 입찰이 참인지 보장하는 것 | 협력하는 노드를 전제함, 프로토콜 자체가 입찰 정보의 진실성을 증명하지는 않음 | 형식·범위만 검사함, 높은 숫자나 이유 문장이 실제 능력을 증명하지는 않음 |
| 잘된 배정의 기준 | manager는 센서의 공간·종류 분포를, contractor는 통신에 유리한 가까운 manager를 고려함 | 최고 confidence로 낙찰하고 미리 정한 gold와의 일치 수를 평가함 |
| 협상 비용 | 공고·입찰 처리와 통신 부담이 들며 자격 조건과 직접 계약 등으로 불필요한 통신을 줄임 | 항상 3곳에 공고하고 입찰·낙찰 메시지를 셈, 별도로 API 호출·토큰 비용이 듦 |
| 실패하는 방식 | 노드 고장과 통신 병목이 문제가 되며 manager가 contractor 고장을 감지하면 재공고할 수 있음 | 형식 오류·유찰·오배정·API 중단 가능성이 있음, 관찰된 실패는 오배정이며 낙찰 후 수행·재공고는 구현 범위 밖임 |

## 4. 로그에 근거한 해석

baseline도 능력 문자열이 입찰 경계를 완전히 지키지는 못했음: [baseline-01.txt L78–83](logs/baseline-01.txt#L78-L83)에서 B가 코드 수정에 `confidence=100`으로 입찰했고 C도 100이어서 먼저 응답한 B가 낙찰받음, 같은 태스크 6이 세 실행 모두 오배정되어 correct가 5/6에 머물렀음. homogeneous는 유효 입찰이 회당 18개로 늘어 messages가 42가 되었고, [homogeneous-04.txt L35–44](logs/homogeneous-04.txt#L35-L44)의 글쓰기 태스크에서는 A/B/C가 모두 100을 내어 A가 낙찰받음. 총 18개 배정 중 17개가 최고값 동점이었고 나머지도 A가 최고여서 A가 전부 가져갔으며 correct가 2/6으로 낮아짐. 이는 동일 능력 설명 아래에서 기존 gold를 유지한 지표와 A 우선 동점 규칙의 결과이고, 일반적인 문제 해결 능력 저하로 해석할 수 없음. overconfident의 C는 분야 밖 12개를 포함한 18개 공고 모두에 95 이상으로 입찰했지만 낙찰은 0개였음. 예를 들어 [overconfident-07.txt L17–18](logs/overconfident-07.txt#L17-L18)의 계산 태스크에서는 C=99보다 A=100이 높았음. 더 특이하게 코드 태스크 5에서 baseline의 C=100이 과신 조건에서는 세 번 모두 99로 관찰되었고, [overconfident-07.txt L65–70](logs/overconfident-07.txt#L65-L70)에서는 B=99와 동점이 되어 B가 이겼음. 태스크 6에서는 C=99가 A/B의 최고 100에 밀렸음. 따라서 추가된 오배정은 C의 독점이 아니라 C가 자기 코드 태스크도 잃은 형태였으며, correct가 4/6으로 감소함. 과신 문구가 모든 확신도를 높인다는 가정은 이 관찰과 맞지 않음. messages는 baseline 평균 36.67에서 37로 거의 같았는데 C가 baseline부터 18개 중 17개 공고에 입찰했기 때문임. 세 번의 소규모 반복으로 인과 효과를 확정할 수는 없지만, 이번 기록은 낙찰 규칙이 역할의 적합성보다 자기 보고 숫자와 동점 순서에 민감함을 보여 줌. 결과 수행 검증이 없는 공고·입찰·낙찰만으로는 이 숫자의 진실성을 확인할 수 없으며, 성과 기록이나 낙찰 후 검증을 추가하려면 실제 실행·평가의 정보와 비용이 더 필요함

단위 테스트 9개와 원본 로그 재집계를 통과했으며, 재집계는 입력·프롬프트·입찰 순서·동점 처리·CSV 지표·토큰까지 대조함
참고 PR의 결과나 모의 응답을 실측치로 사용하지 않았고, 응답 변동과 불리한 배정도 모두 원본 기록에 보존함
