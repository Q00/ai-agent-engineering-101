# game-design-architecture / baseline / 1회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-game-design-architecture-baseline/artifacts/game-design-architecture.json)

'유적의 열두 밤' 개발 착수 자료(설계 제안, 실제 구현/테스트 미수행).

[1] 게임 기획서(규칙+재미 가설)
- 보드 6×6, 최대 12턴. 1턴=플레이어 1행동+월드 반응.
- 핵심 루프: 탐험(안개 타일 공개)→전투(적 조우)→보상(3택1)→탈출(출구 도달).
- 승리: 12턴 이내 출구 타일에서 '탈출' 명령 성공. 패배: 체력 0 또는 12턴 소진 후 미탈출.
- 난이도 곡선: 턴 1-4 적 1종/약, 5-8 적 2종+함정, 9-12 정예+자원 압박. 가설: 후반 압박이 보상 선택의 긴장을 만든다.
- 전투: 공격력-방어력=피해(최소1), 명중은 seed 기반. 가설: 단순 산식+위치 변수로 학습 가능한 전술.
- 보상: 3택1(공격/방어/체력/시야/탈출열쇠). 가설: 매 판 다른 조합이 재플레이 동기를 만든다.
- 튜토리얼: 1턴 고정 시나리오(이동→전투→보상) 강제. 접근성: 키보드 전용 조작, 색맹 대비 팔레트, 텍스트 로그, 폰트 스케일, 스크린리더용 aria-live 로그.

[2] 디렉터리 트리/모듈 책임/의존 방향
src/domain(GameState, rules, combat, map, rng) ← src/application(turnEngine, commands, saveService) ← src/infra(seedRng, clock, localStorageCodec) / src/ui(canvasRenderer, input, a11y). 의존은 domain←application←infra/ui 단방향. domain은 DOM/Canvas/Date/Math.random 미참조.

[3] 타입/시그니처(설계 제안, 미컴파일)
type Vec={x:number;y:number}; type GameState={version:number;seed:string;turn:number;maxTurns:12;board:{w:6;h:6;tiles:Tile[]};player:{hp:number;atk:number;def:number;pos:Vec};enemies:Enemy[];phase:'explore'|'combat'|'reward'|'won'|'lost';rngCursor:number};
type Command={type:'MOVE';dir:'N'|'S'|'E'|'W'}|{type:'ATTACK';targetId:string}|{type:'PICK_REWARD';index:0|1|2}|{type:'ESCAPE'};
type Event={type:'MOVED'|'DAMAGED'|'REWARD_OFFERED'|'TURN_ENDED'|'WON'|'LOST';payload:unknown};
interface Rng{nextInt(maxExclusive:number):number;cursor():number;}
interface SaveCodec{encode(s:GameState):string;decode(raw:string):GameState;migrate(raw:string,from:number):GameState;}
턴 처리 의사코드: applyCommand(state,cmd,rng)->{state',events}: validate phase→resolve command→worldTick(enemies)→turn++→check win/lose→return.

[4] 규칙→모듈→테스트 대응표
- 이동/턴→domain/rules+application/turnEngine→test: 12턴 초과 시 lost.
- seed 맵→domain/map+infra/seedRng→test: 동일 seed 동일 타일 배열.
- 전투→domain/combat→test: 피해 최소1, 명중 재현.
- 저장/복원→application/saveService+infra/localStorageCodec→test: encode→decode→상태 동일, 버전 마이그레이션.
- 화면→ui/canvasRenderer→test: 렌더는 순수 함수(상태→픽셀 명령) 스냅샷.
- 자동 테스트→tests/→CI 없이 로컬 러너.
- 접근성→ui/a11y→test: 키보드만으로 전 루프 완주, aria-live 로그 존재.

[5] 저장 스키마/재현/수용 기준
schemaVersion=1, 필드: version,seed,turn,rngCursor,board,player,enemies,phase. 재현 테스트: 동일 seed+동일 Command[]→동일 Event[] 및 최종 GameState 해시 일치. 수용 기준: (a) 오프라인 로드 성공, (b) 저장→복원 후 이어서 동일 결과, (c) 12턴/승패 규칙 위반 0건, (d) 키보드 전용 완주.

[6] 일정/제외 범위
개발 2명×20h×4주=160h, QA 1명×10h×4주=40h. 확정 추정 합계 24+24+32+24+20+24+12=160h로 개발 예산과 정확히 일치(여유 0). QA 40h는 개발 추정에 미포함된 별도 트랙. 협동 포함 시 +80h=240h로 개발 예산 160h 초과. 제외: 서버·로그인·결제·온라인 협동.

[모순/미확인]
- 기본 범위가 개발 예산 160h에 여유 0 → 일정 리스크, 버퍼 없음.
- QA 40h가 개발 추정 160h에 포함되지 않아 전체 인력 예산(200h)과 개발 예산(160h) 구분 필요.
- 협동 80h는 병렬화해도 사람시간 불변 → 예산 초과 확정.
- 실제 코드 컴파일/실행/테스트는 미수행(설계 제안 단계).

### Facts

```json
{
  "board_width": 6,
  "board_height": 6,
  "max_turns": 12,
  "developer_hours": 160,
  "qa_hours": 40,
  "core_scope_hours": 160,
  "with_coop_hours": 240,
  "coop_fits_budget": false,
  "seeded_replay_required": true,
  "offline_required": true,
  "core_fits_developer_budget": true,
  "core_budget_slack_hours": 0,
  "total_people_hours": 200,
  "implementation_performed": false,
  "tests_executed": false,
  "schema_version": 1
}
```

### Evidence

**1.** 개발 예산: 2명×20h×4주=160h. QA 예산: 1명×10h×4주=40h.

**2.** 기본 범위 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성)=160h.

**3.** 160h(기본 범위)=160h(개발 예산) → coop_fits_budget 판정과 별개로 기본 범위는 정확히 소진, 여유 0.

**4.** 협동 포함: 160+80=240h > 160h 개발 예산 → coop_fits_budget=false. 병렬화해도 사람시간 합계 불변(자료 명시).

**5.** QA 40h는 개발 추정 160h 항목에 없으므로 별도 트랙이며 전체 인력 200h와 개발 160h를 구분.

**6.** 재현 요구: '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과' 및 '저장 후 불러와도 재현' → seeded_replay_required=true, Rng/SaveCodec 주입 인터페이스 분리.

**7.** 오프라인 요구: '브라우저에서 오프라인으로 동작' → offline_required=true, 서버·로그인·결제·온라인 협동 기본 범위 제외.

**8.** 설계 문서/코드 조각은 summary에 텍스트로 제시했으며 실제 컴파일·실행·테스트는 수행하지 않음(미검증).
