# Week 03 보고서 — 실행 기록과 해석 템플릿

**현재 자동 라우팅 설정으로 baseline 1회 완료. 조건 비교와 사용자 해석이 남아 있어 제출용 완성 보고서가 아니다.**
Codex가 실행 하네스와 합성 작업을 작성했다. 아래 결과는 실제 로그에서 옮겼으며 Smith 비교·해석은 사용자가 검토한 뒤 작성한다.

## 1. 설정

- provider: OpenRouter 자동 라우팅. `only`·`order`·`sort` 없음, allow_fallbacks=true, require_parameters=true. 계정 정책과 입력 $0.30/M·출력 $1.20/M 상한을 적용한다.
- 실제 backend는 호출마다 로그의 provider 필드로 확인한다. 라우팅 정책은 세 조건에서 같고 다른 모델로 fallback하지 않는다. 공급자 구현·양자화 차이는 통제하지 못하는 변동 요인이다.
- model: deepseek/deepseek-v4.1-flash.
- temperature=0, max_tokens=512, reasoning.enabled=false 요청.
- A 제품·기술 / B 사업·분석 / C 운영·커뮤니케이션. 정확한 프롬프트는 contract_net.py와 각 실행의 start 로그에 보관.
- 작업당 세 독립 호출, 대화 기억 없음, A→B→C 순서. 최고 확신도에 배정하고 동점은 먼저 입찰한 쪽.
- 명령: `python3 submissions/26622007/week-03/run.py run` (저장소 루트).
- 오류·비용·재시도 정책과 환경은 README.md, config.json, 실행 start 로그를 함께 참고한다.

## 2. 실제 결과

[results.csv](results.csv)의 모든 행을 아래에 표시한다. `—`는 crashed 행의 빈 수치이며 0점이 아니다.
앞선 실패 3회와 Novita 고정 성공 1회는 라우팅 설정·experiment_id가 달라 현재 반복 실험과 분리한다.

| run | condition | tasks | correct | messages | unassigned | misawards | note |
|---|---|---:|---:|---:|---:|---:|---|
| 20260915T121020-423c5498 | baseline | — | — | — | — | — | crashed; CallError: HTTP 429 |
| 20260915T121106-1358bb2f | baseline | — | — | — | — | — | crashed; CallError: HTTP 429 |
| 20260915T121151-75d78906 | baseline | — | — | — | — | — | crashed; CallError: HTTP 429 |
| 20260915T121417-95790cf5 | baseline | 6 | 6 | 36 | 0 | 0 | completed; Novita 고정; parse_fails=0 |
| 20260915T121722-fb1a7bb5 | baseline | 6 | 6 | 35 | 0 | 0 | completed; 자동 라우팅; parse_fails=0 |

### 현재 자동 라우팅 설정

- 실행 `20260915T121722-fb1a7bb5`, 설정 ID `0483e14314802b34`.
- 모든 실제 응답 모델은 `deepseek/deepseek-v4.1-flash`였고 공급자는 9개였다: Novita 4회, Relace 2회, Wafer 3회, Alibaba 2회, Modal 1회, Morph 3회, Parasail 1회, GMICloud 1회, Reka 1회.
- HTTP 18회, 입력 7593 토큰, 출력 1384 토큰, 추론 0 토큰, API 응답 비용 합계 $0.003295322. 비용 필드 누락은 0개다.
- [원본 로그](logs/20260915T121722-fb1a7bb5-baseline.log)의 낙찰은 17·33·49·65·81·97행, 정답 대조는 98–103행, 집계는 104–105행이다.
- 순서대로 A, C, B, B, A, C에게 배정됐다. 메시지 35건 = 공고 18 + 유효 입찰 11 + 낙찰 6. 별도 거절 7건이며 JSON 실패는 0건이다.
- 같은 라우팅 정책으로 baseline 2회, homogeneous 3회, overconfident 3회가 더 실행돼야 조건별 추세를 해석할 수 있다.

### 과거 Novita 고정 설정

실행 `20260915T121417-95790cf5`, 설정 ID `2e2176856b2de858`에서는 18개 응답 모두 Novita였으며 정답 배정 6/6, 메시지 36, API 응답 비용 합계 $0.003780636이었다.
[과거 원본 로그](logs/20260915T121417-95790cf5-baseline.log)를 보존한다. 현재 설정과 합쳐 동일 조건의 반복 결과로 계산하지 않는다.

## 3. Smith (1980) 비교 — 사용자가 작성

필수 논문을 읽고 원문 근거와 실제 구현을 대조한다.

| 비교 항목 | Smith의 분산 센싱 시스템 | 이번 구현에서 확인한 내용 |
|---|---|---|
| 참여자 | 작성 필요 | 작성 필요 |
| 입찰 생성 | 작성 필요 | 작성 필요 |
| 입찰의 진실성 보장 | 작성 필요 | 작성 필요 |
| 잘된 배정의 기준 | 작성 필요 | 작성 필요 |
| 협상 비용 | 작성 필요 | 작성 필요 |
| 실패 방식 | 작성 필요 | 작성 필요 |

## 4. 해석 — 사용자가 작성

baseline 대비 어떤 지표가 변했는가? 대표 로그의 파일·줄 번호를 인용한다.
C의 과신 지시가 실제 입찰에 나타났는가? 낙찰이 유지됐다면 동점·확신도·파싱 실패 중 무엇이 설명하는가?
메시지 비용과 HTTP 요청·토큰 비용은 어떻게 다른가?
여기서 gold는 실제 업무 수행 성능이 아니라 사전 역할 정의에 따른 책임 담당자다. 이 평가 범위와 6개 합성 작업의 한계를 명시한다.
