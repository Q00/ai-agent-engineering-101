# 단계별 작업 계획

한 단계의 설계, 구현, 검증, 커밋을 마친 뒤 다음 단계로 이동한다. 핵심 로직은 작성자가 동작 이유를 설명할 수 있도록 작은 단위로 구현한다. 체크는 실제 완료 시에만 표시한다.

## 단계 0 — 골격 (이번 작업)

- [x] 계정과 학번 매핑 확인: seochanit / 26510121.
- [x] 과제 README, 강의, CI 검사 코드 확인.
- [x] 서버·host·runner 책임과 작업 순서 초안 작성.
- [x] 결과 CSV 헤더 및 보고서 틀 작성.
- [x] PATH/py launcher 미등록을 확인; 후속 점검에서 Anaconda Python 3.12.4를 발견해 기록 정정.
- 골격 커밋은 `git log -- submissions/26510121/week-05`에서 확인한다.

## 단계 1 — 시나리오·환경·측정 설계

- [x] Python 3.12.4 실행 환경 확보 및 버전 기록.
- [x] Codex CLI / gpt-6-luna / low reasoning / temperature 미지정; MCP 2.2.0 설치 및 import 확인.
- [x] 최소 4개 시나리오 작성: 거래 가능, 경계값(reserve = budget), 거래 불가능 사례 포함.
- [x] scenarios.json의 id 고유성, 필수 필드, 정수 가격 확인; 실험 전 커밋 7e8eb8a.
- [x] 역할별 system prompt 초안 작성; 조건별로 동일한 역할 프롬프트 사용.
- [x] 8회 host 실행 제한, 무효/없는 행동의 차례 전환, 거부 후 같은 턴 재시도 규칙 확정.
- [x] correct 및 open 처리 기준을 4주차 기준과 대조; attempts / refusals / accepted moves / host turns를 구분.

Existing ChatGPT login and actual gpt-6-luna MCP calls were verified in the normal user environment. Sandbox-only authentication output was corrected; see ENVIRONMENT.md and STAGE4-5.md.

완료 기준: 실험 조건과 데이터 정의를 설명할 수 있고, 시나리오 커밋이 본 실행보다 앞선다.

## 단계 2 — 인증과 협상 상태

- [x] 보호된 관리 경로에서 negotiation_id와 buyer/seller 토큰 발급.
- [x] 토큰에서 역할과 협상 권한 결정; 에이전트 인자로 신원을 받지 않기.
- [x] Streamable HTTP, 무효 토큰 HTTP 401 및 WWW-Authenticate 검증.
- [x] 다른 협상 ID를 tool error로 거부; 차례 검사는 require_turn으로 구현·직접 검증.
- [x] get_negotiation 구현·확인·커밋 (9267804).

14개 검사가 실제 HTTP 서버/상태 검사에 통과했다. require_turn을 행동 도구에 연결해 실제 차례 밖 행동을 tool error로 거부하는 검증은 단계 3에서 한다. 상태·인증·조회 구현 및 실행 설명은 STAGE2.md, 실패/성공 원본 출력은 checks/에 있다.

완료 기준: 실제 HTTP 요청으로 신원·협상 범위를, 상태 검사로 차례 제한을 증명한다. 실제 행동 tool의 차례 검사는 단계 3과 연계한다.

## 단계 3 — 행동 도구와 한도 강제

- [x] propose 구현·검증·커밋: 정수 가격, 정상 상태 전환 (b1d6d0d).
- [x] accept_proposal 구현·검증·커밋: 상대 활성 제안 및 실제 거래 가격 확인 (6ca709d).
- [x] reject_proposal 구현·검증·커밋 (253a7c7).
- [x] refuse 구현·검증·커밋 (f2130c9).
- [x] server 조건에서 buyer 최대 예산 / seller 최소 가격 검사: 제안과 수락 모두 적용.
- [x] 한도와 같은 가격 허용, 한도를 벗어난 가격 거부, 종료 후 행동 거부 확인.
- [x] 거부 시 협상 상태와 차례가 보존되는지 확인.

실제 HTTP 행동 검사로 단계 2의 차례 밖 행동 거부도 확인했다. stage 2+3 총 32개 테스트 통과, 실패/성공 원본은 checks/stage3-*.txt에 보존했다. 상태 전이와 한도 검사 원리는 STAGE3.md에 있다. 모델 호출은 없다.

완료 기준: 서버 조건에서 한도 밖 거래가 성립하지 않고, 거부 후 같은 턴에 유효한 행동이 가능하다.

## 단계 4 — host와 runner

- [x] tools/list로 목록 조회, tools/call로 실행; 호출자 토큰을 HTTP 헤더에 전달.
- [x] 토큰과 관리자 자격 증명을 모델 입력·콘솔 로그에 노출하지 않기.
- [x] 에이전트가 상태 조회 후 행동하도록 구성; 거부 결과를 모델에 전달.
- [x] 협상 생성, 차례 제어, 종료 및 8 moves 제한 구현.
- [x] 이미 기록된 (run, condition, scenario) 건너뛰기 및 중단 후 재개 구현.
- [x] 원본 콘솔 캡처, 에피소드 CSV 저장, 오류 에피소드 보존 구현.

완료 기준: 예비 에피소드의 상태·CSV·로그가 서로 일치한다.

## 단계 5 — 주입과 실측 검증

- [x] buyer의 seller 제안 조회 결과에 지정 문장을 정확히 삽입.
- [x] raised = max(reserve, budget) + 30 확인; seller에게 주입 숨김.
- [x] 조건 간 같은 시나리오·host·모델·역할 프롬프트·턴 제한 확인.
- [x] 실제 서버 대상 인증 검사 네 가지를 실행하고 auth_checks.txt에 기록.
- [x] 작은 예비 실행으로 집계·로그·재개 기능 확인; 수정과 실패를 별도 커밋.

완료 기준: 요구된 네 검사 결과와 예비 실행 근거가 존재한다.

## 단계 6 — 본 실험

- [ ] 필수 조건 prompt_inject / server_inject 각각 모든 시나리오 3회 이상 실행.
- [ ] 최소 24개 에피소드, 최소 6개 run 원본 로그 저장.
- [ ] run ID를 조건과 반복을 포함해 고유하게 부여.
- [ ] 오류 에피소드도 기록; 실행하지 않은 결과를 채우지 않기.
- [ ] 선택 조건 prompt / server 포함 여부는 호출 예산을 보고 결정; 포함 시 각 시나리오 3회.

완료 기준: 실제 실행 결과와 모든 도구 호출·결과·거부가 추적 가능하다.

## 단계 7 — 분석·보고서·제출

- [ ] CSV를 로그와 대조하고 조건별 집계 및 에피소드 표 작성.
- [ ] 거부 후 같은 턴 유효 행동 횟수 집계; 로그 파일과 줄 번호 인용.
- [ ] FIPA-ACL 비교표와 관측에 근거한 해석 작성.
- [ ] 의존성, 환경, 설정, 실행 명령을 재현 가능하게 기록.
- [ ] 구문 검사, 실제 서버 검사, scripts/check_week05.py 실행.
- [ ] 본인 디렉터리만 변경되었는지 및 비밀정보 누출 여부 확인.
- [ ] 커밋 이력 보존 후 push, upstream PR 제목 [week-05] 26510121.

완료 기준: 구조 검사 통과와 실행 증거가 모두 있고, 6주차 수업 시작 전에 PR이 열렸다.
