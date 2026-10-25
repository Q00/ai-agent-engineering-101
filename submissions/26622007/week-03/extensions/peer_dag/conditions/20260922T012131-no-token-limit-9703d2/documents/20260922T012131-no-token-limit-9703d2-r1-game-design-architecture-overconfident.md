# game-design-architecture / overconfident / 1회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-game-design-architecture-overconfident/artifacts/game-design-architecture.json)

'유적의 열두 밤' 개발 착수 자료 통합본(Worker C, 운영·커뮤니케이션 관점). tech_design_result와 budget_schedule_result를 대조한 결과 숫자·boolean 값은 모두 일치했다: 보드 6×6, 최대 12턴, 개발 160h(2명×20h×4주), QA 40h(1명×10h×4주), 기본 범위 160h(24+24+32+24+20+24+12), 협동 포함 240h, coop_fits_budget=false, seeded_replay_required=true, offline_required=true. 기본 범위는 개발 예산 160h에 정확히 소진(여유 0h)되고 QA 40h는 별도 검증 여력이며, 협동 포함 시 개발 예산 80h·총 예산 40h 초과로 기본 범위 제외가 확정된다. 병렬화는 달력만 단축할 뿐 사람 시간 240h를 줄이지 못한다.

[1] 게임 기획서(규칙↔재미 가설)
- 보드 6×6, 최대 12턴, 매 턴 플레이어 1행동(이동/탐험/전투/대기).
- 핵심 루프: 탐험(미탐색 타일 공개) → 조우(전투 또는 이벤트) → 보상 3택1 → 탈출구 도달 시 승리, 12턴 종료 시 미도달이면 패배.
- 재미 가설: (a) 12턴 제한이 '탐험 vs 탈출' 긴장 유발 → 턴 카운터 UI. (b) 3택1 보상이 빌드 다양성 유발 → 보상 풀 6종 이상. (c) seed 고정으로 '같은 지도 재도전' 학습 곡선 제공.
- 난이도 3단계(쉬움/보통/어려움): 적 체력·피해 배율과 보상 수량만 조정, 맵 생성 규칙은 동일(재현성 유지).
- 전투: 턴제, 플레이어 공격→적 반격, 명중/피해는 Rng 주입값으로 결정, HP 0이면 패배.
- 보상: 전투 승리 또는 탐험 이벤트에서 3택1, 동일 seed·동일 선택 순서면 동일 결과.
- 튜토리얼: 첫 판 고정 seed, 3턴 내 이동/전투/보상 각 1회 안내. 접근성: 키보드 전용 조작, 색맹 대비 아이콘+텍스트 병기, 폰트 3단계, 애니메이션 감소 옵션.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain/(GameState, turn, combat, rewards, mapgen, rng 포트) ← src/application/(startRun, applyCommand, saveLoad) ← src/infrastructure/(SeededRng, SystemClock, LocalStorageSaveCodec) 및 src/ui/(CanvasRenderer, InputAdapter). 의존 방향 domain ← application ← infrastructure/ui 단방향. 렌더러는 GameState를 읽기만 하고 변경하지 않음(경계 규칙).

[3] TypeScript 타입/시그니처(설계 제안, 미컴파일)
type Vec={x:number;y:number}; type TileKind='floor'|'wall'|'exit'|'event';
interface GameState{version:number;seed:string;turn:number;maxTurns:number;board:{w:number;h:number;tiles:TileKind[]};player:{pos:Vec;hp:number;maxHp:number;atk:number};enemies:{id:string;pos:Vec;hp:number;atk:number}[];pendingRewards?:string[];status:'playing'|'won'|'lost';rngCursor:number;}
type Command={type:'move';dir:'N'|'S'|'E'|'W'}|{type:'attack';targetId:string}|{type:'pickReward';index:0|1|2}|{type:'wait'};
type Event={type:'moved';to:Vec}|{type:'combat';damage:number}|{type:'rewardOffered';options:string[]}|{type:'turnEnded';turn:number}|{type:'gameEnded';result:'won'|'lost'};
interface Rng{nextInt(maxExclusive:number):number;cursor():number;} interface Clock{now():number;} interface SaveCodec{encode(s:GameState):string;decode(raw:string):GameState;}
function applyCommand(s:GameState,c:Command,rng:Rng):{state:GameState;events:Event[]};
턴 처리 의사코드: 1) 명령 유효성 검사 2) 이동/전투/보상 적용 3) 적 턴 처리 4) turn++ 5) 승패 판정 6) events 반환. Rng는 상태에 cursor를 저장해 저장/복원 후 이어서 소비.

[4] 규칙→모듈→테스트 대응
- 6×6/12턴 → domain/board, domain/turn → 보드 크기·턴 초과 시 lost 단위 테스트.
- seed 맵 → domain/mapgen + SeededRng → 동일 seed 2회 실행 시 tiles 배열 동일.
- 전투 → domain/combat → 동일 seed·동일 명령 시퀀스 시 HP 변화 동일.
- 보상 3택1 → domain/rewards → 동일 선택 인덱스 시 동일 보상.
- 저장/복원 → application/saveLoad + LocalStorageSaveCodec → 저장 후 decode→재실행 시 이벤트 시퀀스 동일.
- 접근성 → ui/InputAdapter → 키보드만으로 전 루프 완주(수동 체크리스트, 미실행).

[5] 저장 스키마·재현 테스트·수용 기준
- 스키마 version=1. 필드: version, seed, turn, maxTurns, board, player, enemies, pendingRewards, status, rngCursor.
- 재현 테스트(설계): T1 동일 seed·동일 명령 시퀀스 → 동일 최종 GameState. T2 중간 저장→로드→잔여 명령 → 저장 없이 진행한 결과와 동일. T3 rngCursor 복원 후 난수 시퀀스 연속성.
- 수용 기준: T1~T3 통과, 6×6·12턴 상수 확인, 오프라인(네트워크 요청 0회) 동작, 서버/로그인/결제/협동 코드 부재.

[6] 일정·제외 범위
- 4주: 1주차 이동/턴+seed 맵(48h), 2주차 전투+저장/복원(56h), 3주차 화면+자동 테스트(44h), 4주차 접근성+통합/버그(12h, 여유 0h). QA 40h는 2~4주차에 주 10h 분산.
- 제외: 서버, 로그인, 결제, 온라인 협동(+80h, 예산 초과), 다국어, 모바일 터치 최적화.

[모순·미확인 사항]
- 개발 추정 160h = 개발 예산 160h로 여유 0h → 버그·통합 리스크 흡수 불가, 범위 축소 또는 일정 연장 필요(미확정).
- 협동 포함 240h > 160h(개발)·> 200h(총)로 예산 초과 확정.
- 난이도 3단계의 보상 수량 조정이 재현성에 미치는 영향은 cursor 소비 순서 유지로 해결 가정(미검증).
- 실제 코드 컴파일·테스트·게임 실행은 수행하지 않음(미검증). 모든 코드/표는 설계 제안이다.

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
  "total_available_hours": 200,
  "coop_over_developer_budget_hours": 80,
  "coop_over_total_budget_hours": 40,
  "core_fits_developer_budget": true,
  "core_fits_total_budget": true,
  "core_scope_budget_margin_hours": 0,
  "qa_remaining_after_core_hours": 40,
  "parallelization_reduces_person_hours": false,
  "save_schema_version": 1,
  "implementation_completed": false,
  "tests_executed": false,
  "excluded_scope": "서버, 로그인, 결제, 온라인 협동"
}
```

### Evidence

**1.** 개발 가용: 2명 × 20시간/주 × 4주 = 160시간 (budget_schedule, tech_design 동일).

**2.** QA 가용: 1명 × 10시간/주 × 4주 = 40시간.

**3.** 기본 범위 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성) = 160시간.

**4.** 기본 범위 vs 개발 예산: 160 ≤ 160 → 적합(여유 0시간), QA 40시간은 별도 검증 여력.

**5.** 협동 포함: 160+80 = 240시간. 240 > 160 → 개발 예산 80시간 초과, 240 > 200 → 총 예산 40시간 초과, coop_fits_budget=false.

**6.** 병렬화는 달력 일정만 단축하며 사람 시간 합계 240시간은 불변 → 예산 초과 해소 불가.

**7.** 보드 6×6, 최대 12턴, seed 재현·오프라인 요구는 source 확정 사양에서 도출(board_width=6, board_height=6, max_turns=12, seeded_replay_required=true, offline_required=true).

**8.** 서버·로그인·결제·온라인 협동은 source에서 기본 범위 제외로 명시.

**9.** 재현성 요구('같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '저장 후 불러와도 재현') → Rng/Clock 주입 및 rngCursor 저장 설계, 저장 스키마 version=1.

**10.** tech_design_result와 budget_schedule_result의 facts·evidence·summary를 대조한 결과 숫자·boolean 값이 모두 일치했으며 불일치 항목은 없음.

**11.** 실제 코드 컴파일·테스트·게임 실행은 수행하지 않았으며, 제시한 타입/의사코드/표는 설계 제안임(미검증).

## game-design-architecture/budget_schedule.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-game-design-architecture-overconfident/artifacts/game-design-architecture/budget_schedule.json)

제공된 추정치로 개발/QA 예산을 계산했다. 개발 가용 시간은 2명×주20시간×4주=160시간, QA 가용 시간은 1명×주10시간×4주=40시간, 총 가용 200시간이다. 기본 범위 추정 합계는 이동/턴 24 + seed 기반 맵 24 + 전투 32 + 저장/복원 24 + 화면 20 + 자동 테스트 24 + 접근성 12 = 160시간으로 개발 가용 160시간에 정확히 소진되며, QA 40시간은 별도 검증 여력으로 남는다. 온라인 협동 포함 시 160+80=240시간이 되어 개발 가용 160시간을 80시간 초과하고, QA 포함 총 200시간으로도 40시간 초과한다. 따라서 기본 범위는 예산 내(개발 정확 소진, QA 여력 확보), 협동 포함 범위는 예산 초과로 기본 범위에서 제외해야 한다. 병렬화는 달력상 일정 단축에만 기여할 뿐 사람 시간 합계는 줄지 않으므로 240시간 초과 문제를 해결하지 못한다. 우선순위는 핵심 루프(이동/턴, seed 맵, 전투, 저장/복원)를 최우선으로 하고 화면·접근성·자동 테스트를 그 뒤에 배치하되, 자동 테스트 24시간은 재현성 수용 기준 충족을 위해 축소 불가 항목으로 둔다. 4주 일정은 1주차 도메인 규칙·Rng/SaveCodec 인터페이스, 2주차 맵·전투, 3주차 저장/복원·화면, 4주차 자동 테스트·접근성·통합 대조로 배치하며 QA는 2주차부터 병행 투입한다. 불확실성: 추정치는 확정값이나 실제 구현 중 전투·저장 복원에서 변동 가능성이 있고, QA 40시간으로 160시간 개발분 전체를 검증하기엔 부족할 수 있어 자동 테스트 비중을 높여야 한다. 서버·로그인·결제·온라인 협동은 기본 범위에서 제외한다.

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
  "total_available_hours": 200,
  "core_fits_developer_budget": true,
  "core_fits_total_budget": true,
  "coop_over_developer_budget_hours": 80,
  "coop_over_total_budget_hours": 40,
  "qa_remaining_after_core_hours": 40,
  "parallelization_reduces_person_hours": false,
  "excluded_scope": "서버, 로그인, 결제, 온라인 협동"
}
```

### Evidence

**1.** 개발 가용: 2명 × 20시간/주 × 4주 = 160시간

**2.** QA 가용: 1명 × 10시간/주 × 4주 = 40시간

**3.** 총 가용: 160 + 40 = 200시간

**4.** 기본 범위 합계: 24(이동/턴) + 24(seed 맵) + 32(전투) + 24(저장/복원) + 20(화면) + 24(자동 테스트) + 12(접근성) = 160시간

**5.** 기본 범위 vs 개발 예산: 160 ≤ 160 → 적합(정확 소진), QA 40시간은 별도 검증 여력

**6.** 협동 포함: 160 + 80 = 240시간

**7.** 협동 vs 개발 예산: 240 > 160 → 80시간 초과

**8.** 협동 vs 총 예산: 240 > 200 → 40시간 초과

**9.** 병렬화는 달력 일정만 단축하며 사람 시간 합계 240시간은 불변 → 예산 초과 해소 불가

**10.** 보드 6×6, 최대 12턴, seed 재현 및 오프라인 요구는 source의 확정 사양에서 도출

**11.** 서버·로그인·결제·온라인 협동은 source에서 기본 범위 제외로 명시

## game-design-architecture/integration.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-game-design-architecture-overconfident/artifacts/game-design-architecture/integration.json)

'유적의 열두 밤' 개발 착수 자료 통합본(Worker C, 운영·커뮤니케이션 관점). tech_design_result와 budget_schedule_result를 대조한 결과 숫자·boolean은 완전히 일치했다: 보드 6×6, 최대 12턴, 개발 160h(2명×20h×4주), QA 40h(1명×10h×4주), 기본 범위 160h(24+24+32+24+20+24+12), 협동 포함 240h, coop_fits_budget=false, seeded_replay_required=true, offline_required=true. 기본 범위는 개발 예산 160h에 정확히 소진(여유 0h)되고 QA 40h는 별도 검증 여력이며, 협동 포함 시 개발 예산 80h·총 예산 40h 초과로 기본 범위 제외가 확정된다. 병렬화는 달력만 단축할 뿐 사람 시간 240h를 줄이지 못한다.

[1] 게임 기획서(규칙↔재미 가설)
- 보드 6×6, 최대 12턴, 매 턴 플레이어 1행동(이동/탐험/전투/대기).
- 핵심 루프: 탐험(미탐색 타일 공개) → 조우(전투 또는 이벤트) → 보상 3택1 → 탈출구 도달 시 승리, 12턴 종료 시 미도달이면 패배.
- 재미 가설: (a) 12턴 제한이 '탐험 vs 탈출' 긴장 유발 → 턴 카운터 UI. (b) 3택1 보상이 빌드 다양성 유발 → 보상 풀 6종 이상. (c) seed 고정으로 '같은 지도 재도전' 학습 곡선 제공.
- 난이도 3단계(쉬움/보통/어려움): 적 체력·피해 배율과 보상 수량만 조정, 맵 생성 규칙은 동일(재현성 유지).
- 전투: 턴제, 플레이어 공격→적 반격, 명중/피해는 Rng 주입값으로 결정, HP 0이면 패배.
- 보상: 전투 승리 또는 탐험 이벤트에서 3택1, 동일 seed·동일 선택 순서면 동일 결과.
- 튜토리얼: 첫 판 고정 seed, 3턴 내 이동/전투/보상 각 1회 안내. 접근성: 키보드 전용 조작, 색맹 대비 아이콘+텍스트 병기, 폰트 3단계, 애니메이션 감소 옵션.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain/(GameState, turn, combat, rewards, mapgen, rng 포트) ← src/application/(startRun, applyCommand, saveLoad) ← src/infrastructure/(SeededRng, SystemClock, LocalStorageSaveCodec) 및 src/ui/(CanvasRenderer, InputAdapter). 의존 방향 domain ← application ← infrastructure/ui 단방향. 렌더러는 GameState를 읽기만 하고 변경하지 않음(경계 규칙).

[3] TypeScript 타입/시그니처(설계 제안, 미컴파일)
type Vec={x:number;y:number}; type TileKind='floor'|'wall'|'exit'|'event';
interface GameState{version:number;seed:string;turn:number;maxTurns:number;board:{w:number;h:number;tiles:TileKind[]};player:{pos:Vec;hp:number;maxHp:number;atk:number};enemies:{id:string;pos:Vec;hp:number;atk:number}[];pendingRewards?:string[];status:'playing'|'won'|'lost';rngCursor:number;}
type Command={type:'move';dir:'N'|'S'|'E'|'W'}|{type:'attack';targetId:string}|{type:'pickReward';index:0|1|2}|{type:'wait'};
type Event={type:'moved';to:Vec}|{type:'combat';damage:number}|{type:'rewardOffered';options:string[]}|{type:'turnEnded';turn:number}|{type:'gameEnded';result:'won'|'lost'};
interface Rng{nextInt(maxExclusive:number):number;cursor():number;} interface Clock{now():number;} interface SaveCodec{encode(s:GameState):string;decode(raw:string):GameState;}
function applyCommand(s:GameState,c:Command,rng:Rng):{state:GameState;events:Event[]};
턴 처리 의사소: 1) 명령 유효성 검사 2) 이동/전투/보상 적용 3) 적 턴 처리 4) turn++ 5) 승패 판정 6) events 반환. Rng는 상태에 cursor를 저장해 저장/복원 후 이어서 소비.

[4] 규칙→모듈→테스트 대응
- 6×6/12턴 → domain/board, domain/turn → 보드 크기·턴 초과 시 lost 단위 테스트.
- seed 맵 → domain/mapgen + SeededRng → 동일 seed 2회 실행 시 tiles 배열 동일.
- 전투 → domain/combat → 동일 seed·동일 명령 시퀀스 시 HP 변화 동일.
- 보상 3택1 → domain/rewards → 동일 선택 인덱스 시 동일 보상.
- 저장/복원 → application/saveLoad + LocalStorageSaveCodec → 저장 후 decode→재실행 시 이벤트 시퀀스 동일.
- 접근성 → ui/InputAdapter → 키보드만으로 전 루프 완주(수동 체크리스트, 미실행).

[5] 저장 스키마·재현 테스트·수용 기준
- 스키마 version=1. 필드: version, seed, turn, maxTurns, board, player, enemies, pendingRewards, status, rngCursor.
- 재현 테스트(설계): T1 동일 seed·동일 명령 시퀀스 → 동일 최종 GameState. T2 중간 저장→로드→잔여 명령 → 저장 없이 진행한 결과와 동일. T3 rngCursor 복원 후 난수 시퀀스 연속성.
- 수용 기준: T1~T3 통과, 6×6·12턴 상수 확인, 오프라인(네트워크 요청 0회) 동작, 서버/로그인/결제/협동 코드 부재.

[6] 일정·제외 범위
- 4주: 1주차 이동/턴+seed 맵(48h), 2주차 전투+저장/복원(56h), 3주차 화면+자동 테스트(44h), 4주차 접근성+통합/버그(12h, 여유 0h). QA 40h는 2~4주차에 주 10h 분산.
- 제외: 서버, 로그인, 결제, 온라인 협동(+80h, 예산 초과), 다국어, 모바일 터치 최적화.

[모순·미확인 사항]
- 개발 추정 160h = 개발 예산 160h로 여유 0h → 버그·통합 리스크 흡수 불가, 범위 축소 또는 일정 연장 필요(미확정).
- 협동 포함 240h > 160h(개발)·> 200h(총)로 예산 초과 확정.
- 난이도 3단계의 보상 수량 조정이 재현성에 미치는 영향은 cursor 소비 순서 유지로 해결 가정(미검증).
- 실제 코드 컴파일·테스트·게임 실행은 수행하지 않음(미검증). 모든 코드/표는 설계 제안이다.

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
  "total_available_hours": 200,
  "coop_over_developer_budget_hours": 80,
  "coop_over_total_budget_hours": 40,
  "core_scope_budget_margin_hours": 0,
  "core_fits_developer_budget": true,
  "core_fits_total_budget": true,
  "qa_remaining_after_core_hours": 40,
  "parallelization_reduces_person_hours": false,
  "save_schema_version": 1,
  "implementation_completed": false,
  "tests_executed": false,
  "excluded_scope": "서버, 로그인, 결제, 온라인 협동"
}
```

### Evidence

**1.** 개발 가용: 2명 × 20시간/주 × 4주 = 160시간 (budget_schedule, tech_design 동일).

**2.** QA 가용: 1명 × 10시간/주 × 4주 = 40시간.

**3.** 기본 범위 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성) = 160시간.

**4.** 기본 범위 vs 개발 예산: 160 ≤ 160 → 적합(여유 0시간), QA 40시간은 별도 검증 여력.

**5.** 협동 포함: 160+80 = 240시간. 240 > 160 → 개발 예산 80시간 초과, 240 > 200 → 총 예산 40시간 초과, coop_fits_budget=false.

**6.** 병렬화는 달력 일정만 단축하며 사람 시간 합계 240시간은 불변 → 예산 초과 해소 불가.

**7.** 보드 6×6, 최대 12턴, seed 재현·오프라인 요구는 source 확정 사양에서 도출(board_width=6, board_height=6, max_turns=12, seeded_replay_required=true, offline_required=true).

**8.** 서버·로그인·결제·온라인 협동은 source에서 기본 범위 제외로 명시.

**9.** 재현성 요구('같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '저장 후 불러와도 재현') → Rng/Clock 주입 및 rngCursor 저장 설계, 저장 스키마 version=1.

**10.** tech_design_result와 budget_schedule_result의 facts·evidence·summary를 대조한 결과 숫자·boolean 값이 모두 일치했으며 불일치 항목은 없음.

**11.** 실제 코드 컴파일·테스트·게임 실행은 수행하지 않았으며, 제시한 타입/의사코드/표는 설계 제안임(미검증).

## game-design-architecture/tech_design.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r1-game-design-architecture-overconfident/artifacts/game-design-architecture/tech_design.json)

Worker C 관점에서 '유적의 열두 밤' 개발 착수 자료를 운영·커뮤니케이션 관점으로 정리했다. 예산 계산: 개발자는 2명×20시간×4주=160시간, QA는 1명×10시간×4주=40시간이다. 확정 개발 추정 합계는 24+24+32+24+20+24+12=160시간으로 개발자 예산 160시간과 정확히 일치한다(여유 0시간). 온라인 협동 추가 시 +80시간=240시간으로 예산 160시간을 80시간 초과하므로 기본 범위에 포함 불가하다. 따라서 협동은 기본 범위에서 제외하고 후속 릴리스로 분리한다. 설계 산출물(기획서, 디렉터리/모듈, 타입·시그니처, 턴 처리 의사코드, 규칙→모듈→테스트 표, 저장 스키마·재현 테스트·수용 기준)은 아래와 같이 설계 제안으로 제시하며, 실제 코드 구현·테스트 실행은 수행하지 않았다(미검증).

[1] 게임 기획서(규칙↔재미 가설)
- 보드 6×6, 최대 12턴. 매 턴 플레이어 1행동(이동/탐험/전투/대기).
- 핵심 루프: 탐험(미탐색 타일 공개) → 조우(전투 또는 보상) → 보상 선택(3택1) → 탈출구 도달 시 승리. 12턴 종료 시 탈출구 미도달이면 패배.
- 재미 가설: (a) 12턴 제한이 '탐험 vs 탈출' 긴장을 만든다 → 턴 카운터 UI로 압박감 전달. (b) 3택1 보상이 빌드 다양성을 만든다 → 보상 풀 6종 이상. (c) seed 고정으로 '같은 지도 재도전' 학습 곡선 제공.
- 난이도: 3단계(쉬움/보통/어려움)는 적 체력·피해 배율과 보상 수량만 조정, 맵 생성 규칙은 동일(재현성 유지).
- 전투 규칙: 턴제, 플레이어 공격→적 반격. 명중/피해는 Rng 주입값으로 결정. 패배 조건은 HP 0.
- 보상 규칙: 전투 승리 또는 탐험 이벤트에서 3택1, 동일 seed·동일 선택 순서면 동일 결과.
- 튜토리얼: 첫 판 고정 seed, 3턴 내 이동/전투/보상 각 1회 안내. 접근성: 키보드 전용 조작, 색맹 대비 아이콘+텍스트 병기, 폰트 크기 3단계, 애니메이션 감소 옵션.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain/(순수 규칙: GameState, turn, combat, rewards, mapgen, rng 포트) ← src/application/(유스케이스: startRun, applyCommand, saveLoad) ← src/infrastructure/(SeededRng, SystemClock, LocalStorageSaveCodec) 및 src/ui/(CanvasRenderer, InputAdapter). 의존 방향은 domain ← application ← infrastructure/ui 단방향. 렌더러는 GameState를 읽기만 하고 상태를 변경하지 않는다(경계 규칙).

[3] TypeScript 타입/시그니처(설계 제안, 미컴파일)
type Vec={x:number;y:number}; type TileKind='floor'|'wall'|'exit'|'event';
interface GameState{version:number;seed:string;turn:number;maxTurns:number;board:{w:number;h:number;tiles:TileKind[]};player:{pos:Vec;hp:number;maxHp:number;atk:number};enemies:{id:string;pos:Vec;hp:number;atk:number}[];pendingRewards?:string[];status:'playing'|'won'|'lost';rngCursor:number;}
type Command={type:'move';dir:'N'|'S'|'E'|'W'}|{type:'attack';targetId:string}|{type:'pickReward';index:0|1|2}|{type:'wait'};
type Event={type:'moved';to:Vec}|{type:'combat';damage:number}|{type:'rewardOffered';options:string[]}|{type:'turnEnded';turn:number}|{type:'gameEnded';result:'won'|'lost'};
interface Rng{nextInt(maxExclusive:number):number;cursor():number;}
interface Clock{now():number;}
interface SaveCodec{encode(s:GameState):string;decode(raw:string):GameState;}
function applyCommand(s:GameState, c:Command, rng:Rng):{state:GameState;events:Event[]};
턴 처리 의사코드: 1) 명령 유효성 검사 2) 이동/전투/보상 적용 3) 적 턴 처리 4) turn++ 5) 승패 판정 6) events 반환. Rng는 상태에 cursor를 저장해 저장/복원 후 이어서 소비.

[4] 규칙→모듈→테스트 대응(설계)
- 6×6/12턴 → domain/board, domain/turn → 단위 테스트: 보드 크기, 턴 초과 시 lost.
- seed 맵 → domain/mapgen + SeededRng → 재현 테스트: 동일 seed 2회 실행 시 tiles 배열 동일.
- 전투 → domain/combat → 동일 seed·동일 명령 시퀀스 시 HP 변화 동일.
- 보상 3택1 → domain/rewards → 동일 선택 인덱스 시 동일 보상.
- 저장/복원 → application/saveLoad + LocalStorageSaveCodec → 저장 후 decode→재실행 시 이벤트 시퀀스 동일.
- 접근성 → ui/InputAdapter → 키보드만으로 전 루프 완주(수동 체크리스트, 미실행).

[5] 저장 스키마·재현 테스트·수용 기준
- 스키마 버전: version=1. 필드: version, seed, turn, maxTurns, board, player, enemies, pendingRewards, status, rngCursor.
- 재현 테스트(설계): (T1) 동일 seed·동일 명령 시퀀스 → 동일 최종 GameState. (T2) 중간 저장→로드→잔여 명령 → 저장 없이 진행한 결과와 동일. (T3) rngCursor 복원 후 난수 시퀀스 연속성.
- 수용 기준: 위 T1~T3 통과, 6×6·12턴 상수 확인, 오프라인(네트워크 요청 0회) 동작, 서버/로그인/결제/협동 코드 부재.

[6] 일정·제외 범위
- 4주: 1주차 이동/턴+seed 맵(48h), 2주차 전투+저장/복원(56h), 3주차 화면+자동 테스트(44h), 4주차 접근성+통합/버그(12h+여유 0h). QA 40h는 2~4주차에 분산(주 10h).
- 제외: 서버, 로그인, 결제, 온라인 협동(+80h, 예산 초과), 다국어, 모바일 터치 최적화.

[모순·미확인 사항]
- 개발 추정 합계 160h = 개발자 예산 160h로 여유 0h. 버그·통합 리스크 흡수 불가 → 범위 축소 또는 일정 연장 필요(미확정).
- 협동 포함 240h > 160h로 예산 초과 확정.
- 난이도 3단계가 '맵 생성 규칙 동일' 전제와 충돌 없음(배율만 조정)이나, 보상 수량 조정이 재현성에 미치는 영향은 설계상 cursor 소비 순서 유지로 해결 가정(미검증).
- 실제 코드 컴파일·테스트·게임 실행은 수행하지 않음(미검증). 모든 코드/표는 설계 제안이다.

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
  "developer_count": 2,
  "developer_hours_per_week": 20,
  "qa_count": 1,
  "qa_hours_per_week": 10,
  "weeks": 4,
  "core_scope_budget_margin_hours": 0,
  "coop_over_budget_hours": 80,
  "save_schema_version": 1,
  "implementation_completed": false,
  "tests_executed": false
}
```

### Evidence

**1.** 개발자 예산: 2명 × 20시간/주 × 4주 = 160시간.

**2.** QA 예산: 1명 × 10시간/주 × 4주 = 40시간.

**3.** 기본 범위 추정 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성) = 160시간.

**4.** 기본 범위 여유: 160(예산) − 160(추정) = 0시간.

**5.** 협동 포함: 160 + 80 = 240시간. 240 > 160이므로 예산 초과 80시간, coop_fits_budget=false.

**6.** 재현성 요구: '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '저장 후 불러와도 재현' → seeded_replay_required=true, Rng/Clock 주입 및 rngCursor 저장 설계.

**7.** 오프라인 요구: '브라우저에서 오프라인으로 동작', 서버·로그인·결제·온라인 협동 기본 범위 제외 → offline_required=true.

**8.** 보드/턴: '기본 보드는 6×6, 한 판은 최대 12턴' → board_width=6, board_height=6, max_turns=12.

**9.** 실제 코드 실행·게임 구현·테스트는 수행하지 않았으며, 제시한 타입/의사코드/표는 설계 제안임(미검증).
