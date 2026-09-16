# 실행 근거와 현재 상태

## 실험 전 확정

- 공식 기준: upstream `94bff56`, week-03 README·check_week03.py.
- `7cc2955`: 6개 태스크·gold·v2.1 설계 사전 커밋.
- `cff8e14`: 입찰 파싱·고정 선정 규칙.
- `c7067a8`: SQLite 상태·메시지/이벤트·가드·롤백 검사.
- `ebbee99`: 공통/조건별 프롬프트, OpenAI 호환 SDK, 재시도 0.
- `6f14042`: CLI·실험 보존·중단 재개. 최초 무료 GLM 실행 코드.
- `f37a3c6`: 대체 설정 파일 선택. Nemotron 비교 실행 코드.
- `c1497e3`: 독립 실험의 원본을 이름공간으로 구분해 제출 CSV로 모으는 후처리.

## API 시도

| 시도 | 시각 UTC | API·설정 | 결과 |
|---|---|---|---|
| GLM baseline 1 | 2026-09-15 23:59:31 시작 | OpenRouter · z-ai/glm-5.2:free | 첫 호출 HTTP 429, 빈 counts의 crashed row 보존 |
| GLM baseline 2 | 2026-09-16 00:00:43 시작 | 같은 설정 | UTC 날짜 변경 뒤에도 첫 호출 HTTP 429 |
| Nemotron 비교 | 2026-09-16 00:04:56 시작 | OpenRouter · nvidia/nemotron-3.5-lightning:free | baseline 1·homogeneous 1 완료, overconfident 도중 00:29:38 UTC HTTP 429로 종료 코드 2 |

무료 모델 목록에 존재하는 것과 실제 API가 응답하는 것은 다르다. GLM의 429를 무료 모델 전체의 장애나 계정 일일 한도 확정으로 단정하지 않는다. 두 원문 요청 실패는 그대로 보존했다. 새로운 키·계정·유료 전환은 하지 않았다.

공통 설정: temperature 0, max_tokens 1024, SDK timeout 60초, 계약 240초, 호출 전 4초, SDK/하네스 자동 재시도 0. OpenRouter 내부의 같은 모델 제공자 라우팅은 서비스 기본값이며 실제 provider 정보는 원응답에 남는다. 해당 외부 라우팅까지 완전히 고정했다고 주장하지 않는다.

실제 baseline 1에서 최대 호출 지연은 107.0초였다. SDK timeout=60 설정을 전체 경과시간의 엄격한 상한으로 해석하지 않는다. 단조 시계의 계약 deadline 검사는 응답 접수 시 별도로 적용한다.

## 검증

- Python 단위·실제 SQLite·SDK HTTP fixture·CLI 통합 테스트 **37개 통과**(6.65초). 테스트용 API 응답은 실제 결과표에 포함하지 않는다.
- basedpyright 오류·경고 0, Ruff 검사 통과, Python no-excuse 검사 21개 파일 위반 0.
- 상태·선정·API·집계·실행 기록을 모듈별로 나눴으며 source file당 250 순수 코드 줄 이하다. JSON과 DB 데이터는 경계에서 형식으로 파싱한다.
- API 키·개인 설정 파일·가상환경은 제출하지 않는다.
- 2026-09-16 14:38 KST 공식 구조 검사: 종료 코드 1. 부족 항목은 homogeneous 반복(1/3), overconfident 시도(2/3), 루트 로그 수(8/9)다. baseline 행은 GLM 실패 2 + Nemotron 완료 1로 구조상 3행이지만, **완료된 Nemotron baseline은 1회**다. 구조 검사가 의미 있는 실험 완성을 보장하지 않는다.
- 최종 의미 검증: 완료 2/9, 중단 4회(GLM 2 + Nemotron 2). SQLite에서 완료 두 실행의 gold·메시지·미배정·오배정을 독립 재계산해 CSV와 일치함을 확인했다. 실행 소스 해시 유지, 잠금 해제, 원본 로그 사본의 SHA-256 일치 확인.
- API 키 내용 대조 검사 통과, 변경은 자신의 week-03 폴더 안으로 한정된다. PR/원격 푸시는 하지 않았다.

## 2026-09-16 14:38 KST 재개

사용자의 “7회 될 때까지” 요청으로 같은 설정을 재개했다. run 4(overconfident)는 첫 호출 HTTP 429로 중단됐다. 완료 횟수는 baseline 1·homogeneous 1·overconfident 0으로 유지된다. 실패 counts는 공란이며 원본 로그를 보존했다.

현재 작업에 시간당 재개 heartbeat `ax-3-7`를 활성화했다. 매 주기 배치 한 번만 재개하고, 같은 429는 반복 알림 없이 보존한다. 조건별 완료 3회 달성 후 보고서·공식 검사·옵시디언을 갱신하고 heartbeat를 중지한다. 유료 전환이나 PR 제출은 포함하지 않는다.

## 2026-09-16 14:48 KST 수동 재시도

사용자의 재시도 지시로 같은 설정을 실행했다. run 5(overconfident)는 첫 호출 HTTP 429로 종료 코드 2 중단. 완료 횟수는 1/1/0으로 유지된다. 실패 원문·공란 지표를 보존하고 제출 CSV·보고서 표를 동기화했다. 시간당 자동 재개는 유지한다.
