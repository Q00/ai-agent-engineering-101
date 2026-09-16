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
| Nemotron 비교 | 2026-09-16 00:04:56 시작 | OpenRouter · nvidia/nemotron-3.5-lightning:free | 별도 실험 폴더에서 실행, 결과는 원본 CSV·로그 참조 |

무료 모델 목록에 존재하는 것과 실제 API가 응답하는 것은 다르다. GLM의 429를 무료 모델 전체의 장애나 계정 일일 한도 확정으로 단정하지 않는다. 두 원문 요청 실패는 그대로 보존했다. 새로운 키·계정·유료 전환은 하지 않았다.

공통 설정: temperature 0, max_tokens 1024, SDK timeout 60초, 계약 240초, 호출 전 4초, SDK/하네스 자동 재시도 0. OpenRouter 내부의 같은 모델 제공자 라우팅은 서비스 기본값이며 실제 provider 정보는 원응답에 남는다. 해당 외부 라우팅까지 완전히 고정했다고 주장하지 않는다.

실제 baseline 1에서 최대 호출 지연은 107.0초였다. SDK timeout=60 설정을 전체 경과시간의 엄격한 상한으로 해석하지 않는다. 단조 시계의 계약 deadline 검사는 응답 접수 시 별도로 적용한다.

## 검증

- Python 단위·실제 SQLite·SDK HTTP fixture·CLI 통합 테스트 **37개 통과**(6.65초). 테스트용 API 응답은 실제 결과표에 포함하지 않는다.
- basedpyright 오류·경고 0, Ruff 검사 통과, Python no-excuse 검사 21개 파일 위반 0.
- 상태·선정·API·집계·실행 기록을 모듈별로 나눴으며 source file당 250 순수 코드 줄 이하다. JSON과 DB 데이터는 경계에서 형식으로 파싱한다.
- API 키·개인 설정 파일·가상환경은 제출하지 않는다.
- 최종 공식 구조 검사와 실험 충족 여부는 실행 종료 후 기록한다. 현재 PR/원격 푸시는 하지 않았다.
