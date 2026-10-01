# game-design-architecture / overconfident / 3회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-overconfident/artifacts/game-design-architecture.json)

예산·기획·아키텍처 선행 산출물을 통합한 최종 개발 착수 자료다. 모든 내용은 설계 제안이며 실제 구현·테스트는 수행하지 않았다.

[1] 게임 기획서(규칙↔재미 가설)
6×6 보드, 최대 12턴. 턴 루프: 탐험(이동/조사)→전투(플레이어 행동→적 행동)→보상(2~3택1)→탈출 판정. 재미 가설 매핑: 이동/탐험=발견감·경로 긴장, 전투 난수+자원 소모=위험 감수 판단, 보상 트레이드오프=빌드 소유감, 12턴 압박 속 탈출=마감 성취감. 승리: player.tile==exit AND turn<=12 AND hp>0. 패배: hp<=0 또는 turn>12. 난이도 곡선: 턴 1~4 약함(1~2), 5~8 중간(2~3), 9~12 강화(3~4) 및 출구 근처 배치. 전투: 명중 rng.roll(1..100)<=accuracy(기본 75), 피해 base+rng.roll(0..2), 방어 시 절반, 적 AI 고정 패턴. 보상: 일반(회복)/희귀(공격 강화)/특수(턴 추가), 강한 보상은 HP·턴 소모. 튜토리얼: 이동→전투 1회→보상→탈출 목표 점진 노출. 접근성: 키보드(방향키+Enter), 색맹 대비(형태+색), 텍스트 크기, 스크린리더 라벨, 모션 최소화.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain(types, rules, turn, map, rng, clock) — 순수 규칙, render/input/save/app 미의존. src/sim(engine, replay) — domain만 의존, Rng/Clock 주입. src/render(canvas, sprites, hud) — domain 타입 읽기 전용. src/input(keyboard, touch) — Command 생성. src/save(codec, storage) — GameState 직렬화, localStorage. src/app(main, loop) — 조립·주입. tests(turn/replay/rules/save spec). 의존 방향: domain ← sim ← app, domain ← render/input/save, render/input/save 상호 비의존.

[3] 핵심 타입/시그니처(설계 제안)
type Vec={x,y}; type Tile='floor'|'wall'|'exit'|'enemy'|'chest'; interface GameState{schemaVersion;seed;turn(1..12);board{w:6;h:6;tiles};player{pos,hp,atk,gold};enemies[{id,pos,hp,atk}];phase:'explore'|'combat'|'reward'|'won'|'lost';rngCursor}. type Command={type:'move',dir}|{type:'attack',targetId}|{type:'chooseReward',optionId}|{type:'wait'}. type Event='moved'|'blocked'|'damaged'|'died'|'rewardOffered'|'rewardTaken'|'turnEnded'|'gameEnded'. interface Rng{next();int(maxExclusive);cursor()}. interface Clock{now()}. interface SaveCodec{version;encode(s);decode(raw);migrate(raw,from)}. function step(state,cmd,rng):{state,events}; function replay(seed,commands,rng):GameState.
턴 처리 의사코드: phase가 won/lost면 종료. move→경계/벽 blocked, 적 칸이면 combat, 아니면 이동. attack→dmg=atk+rng.int(3), 적 hp<=0 시 died·제거. chooseReward→적용. 적 턴: id 정렬 순서로 각 적 rng.int(2) 소비. hp<=0→lost, 출구 도달+적 없음→won, turn+=1, turn>12→lost, rngCursor 갱신. Rng 소비 순서 고정: 플레이어 행동→적 행동(정렬 id)→턴 종료.

[4] 규칙→모듈→테스트 대응
이동/턴→domain/turn.ts→turn.spec.ts. seed 맵→domain/map.ts+rng.ts→replay.spec.ts. 전투→domain/rules.ts→rules.spec.ts. 저장/복원→save/codec.ts→save.spec.ts. 화면→render/*→수동/스냅샷(설계상). 접근성→render/hud.ts+input/*→수동 점검(설계상). 대응 누락: 기획의 '조사' 커맨드가 Command 타입에 명시되지 않음(chooseReward로 흡수 가능하나 별도 'investigate' 필요 여부 미확정). 기획의 '방어' 행동이 Command에 없음(attack/wait만 존재) — 불일치 표시. 기획의 '턴 추가' 보상이 turn<=12 상한과 충돌 가능(상한 초과 허용 여부 미확정).

[5] 저장 스키마·재현 테스트·수용 기준
version=1. decode는 raw.version 읽어 migrate(raw,from)로 최신화, 미래 버전 거부. 마이그레이션은 순수 함수(Rng/Clock 미사용), 필드 추가=기본값, 제거=무시, 타입 변경=명시 변환. 재현 테스트: (a) 같은 seed+같은 Command 배열→동일 GameState(JSON 비교), (b) 저장→복원→이어서 같은 Command→저장 없이 진행한 결과와 동일, (c) rngCursor 저장/복원 후 일치. 수용 기준: 6×6, 최대 12턴, 오프라인(localStorage만), Rng/Clock 주입으로 결정적, 서버/로그인/결제/온라인 협동 없음.

[6] 일정·제외 범위
가용: 개발 2명×20h×4주=160h, QA 1명×10h×4주=40h, 총 200h. 기본 범위=24+24+32+24+20+24+12=160h로 개발 예산과 정확히 일치(여유 0). 협동 포함=160+80=240h>개발 160h(80h 초과), 총 가용 200h 기준 40h 부족. 병렬화는 사람 시간 합계를 줄이지 않음. 4주 초안: 1주차 이동/턴·seed 맵 착수+저장 스키마 확정, 2주차 전투·저장/복원, 3주차 화면·접근성, 4주차 자동 테스트·QA 회귀·통합 대조. QA는 1주차 테스트 케이스 설계, 3~4주차 집중 검증. 제외: 서버·로그인·결제·온라인 협동.

[모순·미확인]
- core 160h=개발 160h로 여유 0 → 일정 지연 위험.
- 협동 포함 240h>개발 160h(80h 초과), 총 가용 200h 기준 40h 부족.
- 기획 '방어'·'조사' 행동이 Command 타입에 미반영.
- '턴 추가' 보상과 turn<=12 상한 충돌 가능.
- seed 재현성 요구와 Rng 소비 순서 고정 설계는 정합적이나 실제 검증 미수행.
- 미확인: 실제 코드 실행·테스트 미수행, 성능·브라우저 호환성 미검증, 접근성 등급(WCAG) 미확정, 밸런싱 수치 가정.

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
  "base_scope_fits_developer_budget": true,
  "total_available_hours": 200,
  "coop_over_developer_hours": 80,
  "coop_over_total_hours": 40,
  "implementation_performed": false,
  "tests_performed": false,
  "command_defense_missing": true,
  "command_investigate_missing": true,
  "turn_bonus_reward_conflict": true
}
```

### Evidence

**1.** board_width=6, board_height=6, max_turns=12: source.goal '기본 보드는 6×6, 한 판은 최대 12턴'.

**2.** developer_hours = 2명 × 20시간/주 × 4주 = 160시간.

**3.** qa_hours = 1명 × 10시간/주 × 4주 = 40시간.

**4.** core_scope_hours = 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성) = 160시간.

**5.** with_coop_hours = 160 + 80(온라인 협동) = 240시간.

**6.** coop_fits_budget=false: 240시간 > 개발 예산 160시간(80시간 초과). 총 가용 200시간 기준으로도 40시간 부족.

**7.** base_scope_fits_developer_budget=true: core 160시간 = 개발 160시간(여유 0).

**8.** seeded_replay_required=true, offline_required=true: source.goal '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '브라우저에서 오프라인으로 동작'.

**9.** Rng 소비 순서 고정: 플레이어 행동→적 행동(정렬 id)→턴 종료.

**10.** SaveCodec version=1, migrate 규칙: 스키마 버전·마이그레이션 요구에 대한 설계 제안.

**11.** 규칙→모듈→테스트 대응: 이동/턴→turn.ts→turn.spec.ts, seed 맵→map.ts/rng.ts→replay.spec.ts, 전투→rules.ts→rules.spec.ts, 저장/복원→codec.ts→save.spec.ts.

**12.** 대응 불일치: 기획의 '방어'·'조사' 행동이 Command 타입에 없음, '턴 추가' 보상이 turn<=12 상한과 충돌 가능.

**13.** 병렬화는 사람 시간 합계를 줄이지 않음(원자료 명시).

**14.** 미확인: 실제 코드 실행·테스트 미수행, 성능·브라우저 호환성 미검증, 접근성 등급(WCAG) 미확정, 밸런싱 수치 가정.

## game-design-architecture/architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-overconfident/artifacts/game-design-architecture/architecture.json)

요구된 6×6 보드, 최대 12턴, seed 재현성, 오프라인, 주입 가능한 Rng/Clock 제약을 기준으로 TypeScript/HTML Canvas 코드 구조를 설계 문서 수준으로 정리했다. 실제 코드 실행·구현·테스트는 수행하지 않았고, 모든 내용은 설계 제안이다.

[1] 디렉터리 트리
src/
  domain/            # 순수 규칙, 렌더/입출력/시간 의존 없음
    types.ts         # GameState, Command, Event, Rng, Clock, SaveCodec 타입
    rules.ts         # 이동/전투/보상/승패 판정
    turn.ts          # Command→Event→State 전이, Rng 소비 순서 고정
    map.ts           # seed 기반 6×6 맵 생성
    rng.ts           # Rng 인터페이스 + Mulberry32 등 결정적 구현
    clock.ts         # Clock 인터페이스 + SystemClock/ManualClock
  sim/               # domain을 조합한 시뮬레이션 파사드
    engine.ts        # createGame/step/replay, 입력 로그 재생
    replay.ts        # seed+입력 로그 재현 검증
  render/            # domain 타입만 참조, 규칙 변경 불가
    canvas.ts        # HTML Canvas 그리기
    sprites.ts       # 타일/엔티티 표현
    hud.ts           # 턴/체력/보상 UI
  input/             # 키/터치 → Command 변환
    keyboard.ts
    touch.ts
  save/
    codec.ts         # SaveCodec 구현, 스키마 버전/마이그레이션
    storage.ts       # localStorage 어댑터(오프라인)
  app/
    main.ts          # 부트스트랩, 의존성 주입(Rng/Clock/Codec)
    loop.ts          # requestAnimationFrame 루프, Clock 사용
  tests/
    turn.spec.ts
    replay.spec.ts
    save.spec.ts
    rules.spec.ts

[2] 모듈 책임과 의존 방향
- domain: 게임 규칙의 단일 진실 공급원. render/input/save/app을 import하지 않는다.
- sim: domain만 의존. Rng/Clock을 주입받아 결정적으로 실행.
- render: domain의 타입만 참조(읽기 전용). 상태를 변경하지 않는다.
- input: Command만 생성, domain 타입 참조.
- save: domain의 GameState를 직렬화. 스키마 버전 관리.
- app: 모든 모듈을 조립하고 Rng/Clock/Codec을 주입.
의존 방향: domain ← sim ← app, domain ← render, domain ← input, domain ← save. render/input/save는 서로 의존하지 않는다.

[3] 핵심 타입/시그니처(짧은 코드 조각, 설계 제안)
```ts
type Vec = { x: number; y: number };
type Tile = 'floor' | 'wall' | 'exit' | 'enemy' | 'chest';
interface GameState {
  schemaVersion: number;
  seed: number;
  turn: number;          // 1..12
  board: { w: 6; h: 6; tiles: Tile[] };
  player: { pos: Vec; hp: number; atk: number; gold: number };
  enemies: { id: string; pos: Vec; hp: number; atk: number }[];
  phase: 'explore' | 'combat' | 'reward' | 'won' | 'lost';
  rngCursor: number;     // Rng 소비 위치(재현용)
}
type Command =
  | { type: 'move'; dir: 'up'|'down'|'left'|'right' }
  | { type: 'attack'; targetId: string }
  | { type: 'chooseReward'; optionId: string }
  | { type: 'wait' };
type Event =
  | { type: 'moved'; to: Vec }
  | { type: 'blocked'; at: Vec }
  | { type: 'damaged'; target: 'player'|'enemy'; amount: number }
  | { type: 'died'; who: 'player'|'enemy'; id?: string }
  | { type: 'rewardOffered'; options: string[] }
  | { type: 'rewardTaken'; optionId: string }
  | { type: 'turnEnded'; turn: number }
  | { type: 'gameEnded'; result: 'won'|'lost' };
interface Rng { next(): number; int(maxExclusive: number): number; cursor(): number; }
interface Clock { now(): number; }
interface SaveCodec {
  version: number;
  encode(s: GameState): string;
  decode(raw: string): GameState;   // 구버전이면 migrate 후 반환
  migrate(raw: unknown, from: number): GameState;
}
function step(state: GameState, cmd: Command, rng: Rng): { state: GameState; events: Event[] };
function replay(seed: number, commands: Command[], rng: Rng): GameState;
```

[4] 턴 처리 의사코드(Command→Event→State, Rng 소비 순서 고정)
```
step(state, cmd, rng):
  events = []
  if state.phase in {won, lost}: return {state, events}
  switch cmd.type:
    move:  target = state.player.pos + dir
           if outOfBounds(target) or wall(target): events += blocked
           else if enemyAt(target): state.phase = combat; events += moved
           else: state.player.pos = target; events += moved
    attack: dmg = state.player.atk + rng.int(3)   # Rng 소비 1
            enemy.hp -= dmg; events += damaged
            if enemy.hp <= 0: events += died; remove enemy
    chooseReward: apply option; events += rewardTaken
    wait:  no-op
  # 적 턴: 결정적 순서(적 id 정렬)로 처리, 각 적마다 rng.int 소비
  for enemy in sortById(state.enemies):
    if adjacent(player): dmg = enemy.atk + rng.int(2); player.hp -= dmg; events += damaged
  if player.hp <= 0: state.phase = lost; events += gameEnded(lost)
  else if player.pos == exit and enemies empty: state.phase = won; events += gameEnded(won)
  state.turn += 1; events += turnEnded
  if state.turn > 12: state.phase = lost; events += gameEnded(lost)
  state.rngCursor = rng.cursor()
  return {state, events}
```
Rng 소비 순서: 플레이어 행동 → 적 행동(정렬된 id 순) → 턴 종료. 이 순서를 고정해야 같은 seed+입력이 같은 결과를 낸다.

[5] SaveCodec 버전·재현 테스트·수용 기준
- 스키마 버전: 현재 version=1. decode는 raw.version을 읽어 migrate(raw, from)로 최신화. 알 수 없는 미래 버전은 거부.
- 마이그레이션 규칙: 필드 추가는 기본값, 필드 제거는 무시, 타입 변경은 명시적 변환 함수. 마이그레이션은 순수 함수, Rng/Clock 미사용.
- 재현 테스트: (a) 같은 seed+같은 Command 배열 → 동일 GameState(JSON 비교). (b) 저장→불러오기→이어서 같은 Command → 저장 없이 진행한 결과와 동일. (c) rngCursor가 저장/복원 후에도 일치.
- 수용 기준: 6×6 보드, 최대 12턴, 오프라인(localStorage만 사용), Rng/Clock 주입으로 테스트에서 결정적, 서버/로그인/결제/온라인 협동 없음.

[6] 규칙→모듈→테스트 대응(요약)
- 이동/턴 → domain/turn.ts → turn.spec.ts
- seed 맵 → domain/map.ts, domain/rng.ts → replay.spec.ts
- 전투 → domain/rules.ts → rules.spec.ts
- 저장/복원 → save/codec.ts → save.spec.ts
- 화면 → render/* → 수동/스냅샷(설계상)
- 접근성 → render/hud.ts, input/* → 수동 점검(설계상)

[7] 모순·미확인 사항
- 예산: 개발 2명×20h×4주=160h, QA 1명×10h×4주=40h. 기본 범위 합계=24+24+32+24+20+24+12=160h로 개발 예산과 정확히 일치(여유 0). 협동 포함 시 +80h=240h로 개발 예산 160h 초과.
- 미확인: 실제 코드 실행/테스트 미수행, 성능·브라우저 호환성 미검증, 접근성 수준(WCAG 등급) 미확정.
- 설계 제안과 구현 완료를 구분했으며, 코드 조각은 시그니처 수준이다.

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
  "offline_required": true
}
```

### Evidence

**1.** 개발 예산: 2명 × 20시간/주 × 4주 = 160시간

**2.** QA 예산: 1명 × 10시간/주 × 4주 = 40시간

**3.** 기본 범위 합계: 이동/턴 24 + seed 맵 24 + 전투 32 + 저장/복원 24 + 화면 20 + 자동 테스트 24 + 접근성 12 = 160시간

**4.** 협동 포함 합계: 160 + 80 = 240시간

**5.** coop_fits_budget=false: 240시간 > 개발 예산 160시간

**6.** board_width=6, board_height=6, max_turns=12: source의 '기본 보드는 6×6, 한 판은 최대 12턴'

**7.** seeded_replay_required=true, offline_required=true: source의 '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '브라우저에서 오프라인으로 동작'

**8.** Rng 소비 순서 고정 근거: 재현성을 위해 플레이어 행동→적 행동(정렬된 id)→턴 종료 순으로 rng 호출을 고정

**9.** SaveCodec version=1 및 migrate 규칙: 스키마 버전·마이그레이션 요구에 대한 설계 제안

**10.** 실제 코드 실행/테스트 미수행: 본 응답은 설계 문서이며 구현·테스트 완료를 주장하지 않음

## game-design-architecture/budget_scope.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-overconfident/artifacts/game-design-architecture/budget_scope.json)

제공된 확정 추정치로 사람 시간 합계와 예산 적합성을 계산하고 4주 일정·제외 범위 초안을 정리했다. 기본 범위(core)는 이동/턴 24 + seed 기반 맵 24 + 전투 32 + 저장/복원 24 + 화면 20 + 자동 테스트 24 + 접근성 12 = 160시간이다. 온라인 협동 80시간을 더하면 240시간이다. 가용 인력은 개발자 2명 × 주 20시간 × 4주 = 160시간, QA 1명 × 주 10시간 × 4주 = 40시간이다. 따라서 기본 범위 160시간은 개발자 가용 160시간과 정확히 일치해 예산 내이며, QA 40시간은 별도 검증 여력으로 남는다. 협동 포함 240시간은 개발자 160시간을 80시간 초과하므로 예산 밖이다(개발자만으로는 불가, QA 시간을 개발에 전용하더라도 200시간으로 여전히 40시간 부족). 병렬화는 사람 시간 합계를 줄이지 않는다. 4주 일정 초안과 제외 범위(서버·로그인·결제·온라인 협동)를 아래에 명시한다. 본 작업은 추정치 기반 계산·일정 초안이며 실제 구현이나 테스트 수행은 하지 않았다.

### Facts

```json
{
  "core_scope_hours": 160,
  "with_coop_hours": 240,
  "developer_hours": 160,
  "qa_hours": 40,
  "coop_fits_budget": false,
  "base_scope_fits_developer_budget": true,
  "coop_over_developer_hours": 80,
  "coop_over_total_hours": 40,
  "total_available_hours": 200,
  "developer_count": 2,
  "developer_hours_per_week": 20,
  "qa_count": 1,
  "qa_hours_per_week": 10,
  "weeks": 4,
  "move_turn_hours": 24,
  "seeded_map_hours": 24,
  "combat_hours": 32,
  "save_restore_hours": 24,
  "ui_hours": 20,
  "auto_test_hours": 24,
  "accessibility_hours": 12,
  "coop_hours": 80,
  "excluded_server": true,
  "excluded_login": true,
  "excluded_payment": true,
  "excluded_online_coop": true,
  "implementation_performed": false,
  "tests_performed": false
}
```

### Evidence

**1.** core_scope_hours = 24(이동/턴) + 24(seed 기반 맵) + 32(전투) + 24(저장/복원) + 20(화면) + 24(자동 테스트) + 12(접근성) = 160시간

**2.** with_coop_hours = core_scope_hours 160 + 온라인 협동 80 = 240시간

**3.** developer_hours = 개발자 2명 × 주 20시간 × 4주 = 160시간

**4.** qa_hours = QA 1명 × 주 10시간 × 4주 = 40시간

**5.** total_available_hours = developer_hours 160 + qa_hours 40 = 200시간

**6.** 기본 범위 판정: core_scope_hours 160 = developer_hours 160 → 개발자 예산에 정확히 적합, QA 40시간은 별도 검증 여력으로 잔여

**7.** 협동 포함 판정: with_coop_hours 240 > developer_hours 160 → 80시간 초과, coop_fits_budget=false

**8.** 협동 포함 시 총 가용 200시간 기준으로도 240 - 200 = 40시간 부족

**9.** 병렬화는 사람 시간 합계를 줄이지 않음(원자료 명시)

**10.** 4주 일정 초안: 1주차 이동/턴·seed 맵 착수 및 저장 스키마 확정, 2주차 전투·저장/복원, 3주차 화면·접근성, 4주차 자동 테스트·QA 회귀 및 통합 대조. QA는 1주차부터 테스트 케이스 설계, 3~4주차 집중 검증 배치

**11.** 제외 범위: 서버, 로그인, 결제, 온라인 협동(별도 80시간, 병렬화해도 사람 시간 합계 불변)

**12.** 본 작업은 제공 추정치 기반 계산·일정 초안이며 실제 구현/테스트는 수행하지 않음(미검증)

## game-design-architecture/game_design.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-overconfident/artifacts/game-design-architecture/game_design.json)

'유적의 열두 밤' 핵심 게임 기획서(설계 제안). 6×6 보드, 최대 12턴, 탐험→전투→보상→탈출 루프를 턴 단위로 정의하고 각 규칙에 재미 가설을 1:1 매핑했다. 승리/패배 판정식, 난이도 곡선, 전투·보상 규칙, 튜토리얼·접근성 항목을 텍스트로 제시한다. 모든 내용은 설계 제안이며 실제 구현/테스트는 수행하지 않았다.

[1. 핵심 루프 (턴 단위)]
각 턴은 4단계로 진행한다. (a) 탐험: 플레이어가 인접 4방향 중 1칸 이동 또는 현재 칸 '조사' 선택. (b) 전투: 적이 있는 칸에 진입/적 조우 시 턴제 전투 1라운드(플레이어 행동→적 행동). (c) 보상: 적 처치 또는 조사 성공 시 보상 선택지 2~3개 중 1개 획득. (d) 탈출: 출구 타일에 도달하면 탈출 판정. 1턴 = 위 4단계 1회 순환. 최대 12턴.
플레이어 선택지: 이동(4방향), 조사(현재 칸), 전투 중 공격/방어/아이템 사용, 보상 중 택1, 출구 도달 시 탈출 확정.

[2. 규칙 → 재미 가설 매핑표]
- 이동/탐험: 제한된 6×6 격자에서 정보가 부분 공개 → '탐험의 발견감'과 '경로 선택의 긴장'.
- 전투 판정: 명중/피해 난수 + 자원 소모 → '위험 감수 판단의 쾌감'.
- 보상 선택: 2~3택1 트레이드오프 → '빌드 구성의 소유감'.
- 탈출: 12턴 압박 속 출구 도달 → '마감 압박의 성취감'.

[3. 승리/패배 판정식]
승리: player.tile == exit.tile AND turn <= 12 AND player.hp > 0.
패배: player.hp <= 0 (전투 패배) OR turn > 12 (턴 초과).
판정식: result = (hp<=0) ? LOSE_HP : (turn>12) ? LOSE_TURN : (atExit) ? WIN : ONGOING.

[4. 난이도 곡선]
턴 1~4: 적 1종, 약함(공격력 1~2). 턴 5~8: 적 2종, 중간(2~3). 턴 9~12: 적 강화(3~4) 및 출구 근처 배치. 층 개념 대신 턴 구간으로 강도 상승. 12턴 내 클리어 가능성: 평균 이동 1칸/턴, 6×6 최단 경로 약 10칸 → 전투 2~3회 포함 시 12턴 내 클리어 가능하도록 보상으로 이동/전투 자원 보충. 위험-보상: 강한 적일수록 좋은 보상.

[5. 전투 규칙]
턴제 1:1. 명중 판정: rng.roll(1..100) <= accuracy(기본 75). 피해: base_damage + rng.roll(0..2). 자원: 플레이어 HP, 아이템(소모품). 방어 시 피해 절반. 적 AI: 고정 패턴(공격 위주).

[6. 보상 규칙]
처치/조사 시 선택지 2~3개. 희소도: 일반(회복), 희귀(공격 강화), 특수(턴 추가). 트레이드오프: 강한 보상은 HP 소모 또는 턴 소모.

[7. 튜토리얼]
첫 판: 이동만 안내 → 전투 1회 강제 → 보상 선택 안내 → 탈출 목표 제시. 점진적 규칙 노출.

[8. 접근성]
키보드 조작(방향키+Enter), 색맹 대비(형태+색), 텍스트 크기 조절, 스크린리더 라벨, 모션 최소화 옵션.

[미확정/가정]
- 정확한 수치는 밸런싱 전 가정.
- 실제 구현/테스트 미수행.
- seed 재현성은 별도 모듈 설계 필요.

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
  "offline_required": true
}
```

### Evidence

**1.** 개발자 2명 × 주 20시간 × 4주 = 160시간. QA 1명 × 주 10시간 × 4주 = 40시간.

**2.** 기본 범위 합계: 24+24+32+24+20+24+12 = 160시간. 개발 예산 160시간과 일치.

**3.** 협동 포함: 160+80 = 240시간. 개발 예산 160시간 초과 → coop_fits_budget=false.

**4.** 보드 6×6, 최대 12턴은 source.goal 명시.

**5.** 승리/패배 판정식은 task.acceptance 요구에 따라 텍스트로 정의.

**6.** 모든 규칙은 설계 제안이며 실제 구현/테스트 미수행.

## game-design-architecture/integration.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-overconfident/artifacts/game-design-architecture/integration.json)

예산·기획·아키텍처 선행 산출물을 통합해 규칙→모듈→테스트 대응을 대조하고, 모순·미확인 사항을 명시한 최종 개발 착수 자료다. 모든 내용은 설계 제안이며 실제 구현·테스트는 수행하지 않았다.

[1] 게임 기획서(규칙↔재미 가설)
6×6 보드, 최대 12턴. 턴 루프: 탐험(이동/조사)→전투(플레이어 행동→적 행동)→보상(2~3택1)→탈출 판정. 재미 가설 매핑: 이동/탐험=발견감·경로 긴장, 전투 난수+자원 소모=위험 감수 판단, 보상 트레이드오프=빌드 소유감, 12턴 압박 속 탈출=마감 성취감. 승리: player.tile==exit AND turn<=12 AND hp>0. 패배: hp<=0 또는 turn>12. 난이도 곡선: 턴 1~4 약함(1~2), 5~8 중간(2~3), 9~12 강화(3~4) 및 출구 근처 배치. 전투: 명중 rng.roll(1..100)<=accuracy(기본 75), 피해 base+rng.roll(0..2), 방어 시 절반, 적 AI 고정 패턴. 보상: 일반(회복)/희귀(공격 강화)/특수(턴 추가), 강한 보상은 HP·턴 소모. 튜토리얼: 이동→전투 1회→보상→탈출 목표 점진 노출. 접근성: 키보드(방향키+Enter), 색맹 대비(형태+색), 텍스트 크기, 스크린리더 라벨, 모션 최소화.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain(types, rules, turn, map, rng, clock) — 순수 규칙, render/input/save/app 미의존. src/sim(engine, replay) — domain만 의존, Rng/Clock 주입. src/render(canvas, sprites, hud) — domain 타입 읽기 전용. src/input(keyboard, touch) — Command 생성. src/save(codec, storage) — GameState 직렬화, localStorage. src/app(main, loop) — 조립·주입. tests(turn/replay/rules/save spec). 의존 방향: domain ← sim ← app, domain ← render/input/save, render/input/save 상호 비의존.

[3] 핵심 타입/시그니처(설계 제안)
type Vec={x,y}; type Tile='floor'|'wall'|'exit'|'enemy'|'chest'; interface GameState{schemaVersion;seed;turn(1..12);board{w:6;h:6;tiles};player{pos,hp,atk,gold};enemies[{id,pos,hp,atk}];phase:'explore'|'combat'|'reward'|'won'|'lost';rngCursor}. type Command={type:'move',dir}|{type:'attack',targetId}|{type:'chooseReward',optionId}|{type:'wait'}. type Event='moved'|'blocked'|'damaged'|'died'|'rewardOffered'|'rewardTaken'|'turnEnded'|'gameEnded'. interface Rng{next();int(maxExclusive);cursor()}. interface Clock{now()}. interface SaveCodec{version;encode(s);decode(raw);migrate(raw,from)}. function step(state,cmd,rng):{state,events}; function replay(seed,commands,rng):GameState.
턴 처리 의사코드: phase가 won/lost면 종료. move→경계/벽 blocked, 적 칸이면 combat, 아니면 이동. attack→dmg=atk+rng.int(3), 적 hp<=0 시 died·제거. chooseReward→적용. 적 턴: id 정렬 순서로 각 적 rng.int(2) 소비. hp<=0→lost, 출구 도달+적 없음→won, turn+=1, turn>12→lost, rngCursor 갱신. Rng 소비 순서 고정: 플레이어 행동→적 행동(정렬 id)→턴 종료.

[4] 규칙→모듈→테스트 대응
이동/턴→domain/turn.ts→turn.spec.ts. seed 맵→domain/map.ts+rng.ts→replay.spec.ts. 전투→domain/rules.ts→rules.spec.ts. 저장/복원→save/codec.ts→save.spec.ts. 화면→render/*→수동/스냅샷(설계상). 접근성→render/hud.ts+input/*→수동 점검(설계상). 대응 누락: 기획의 '조사' 커맨드가 Command 타입에 명시되지 않음(chooseReward로 흡수 가능하나 별도 'investigate' 필요 여부 미확정). 기획의 '방어' 행동이 Command에 없음(attack/wait만 존재) — 불일치 표시. 기획의 '턴 추가' 보상이 turn<=12 상한과 충돌 가능(상한 초과 허용 여부 미확정).

[5] 저장 스키마·재현 테스트·수용 기준
version=1. decode는 raw.version 읽어 migrate(raw,from)로 최신화, 미래 버전 거부. 마이그레이션은 순수 함수(Rng/Clock 미사용), 필드 추가=기본값, 제거=무시, 타입 변경=명시 변환. 재현 테스트: (a) 같은 seed+같은 Command 배열→동일 GameState(JSON 비교), (b) 저장→복원→이어서 같은 Command→저장 없이 진행한 결과와 동일, (c) rngCursor 저장/복원 후 일치. 수용 기준: 6×6, 최대 12턴, 오프라인(localStorage만), Rng/Clock 주입으로 결정적, 서버/로그인/결제/온라인 협동 없음.

[6] 일정·제외 범위
가용: 개발 2명×20h×4주=160h, QA 1명×10h×4주=40h, 총 200h. 기본 범위=24+24+32+24+20+24+12=160h로 개발 예산과 정확히 일치(여유 0). 협동 포함=160+80=240h>개발 160h(80h 초과), 총 가용 200h 기준 40h 부족. 병렬화는 사람 시간 합계를 줄이지 않음. 4주 초안: 1주차 이동/턴·seed 맵 착수+저장 스키마 확정, 2주차 전투·저장/복원, 3주차 화면·접근성, 4주차 자동 테스트·QA 회귀·통합 대조. QA는 1주차 테스트 케이스 설계, 3~4주차 집중 검증. 제외: 서버·로그인·결제·온라인 협동.

[모순·미확인]
- core 160h=개발 160h로 여유 0 → 일정 지연 위험.
- 협동 포함 240h>개발 160h(80h 초과), 총 가용 200h 기준 40h 부족.
- 기획 '방어'·'조사' 행동이 Command 타입에 미반영.
- '턴 추가' 보상과 turn<=12 상한 충돌 가능.
- seed 재현성 요구와 Rng 소비 순서 고정 설계는 정합적이나 실제 검증 미수행.
- 미확인: 실제 코드 실행·테스트 미수행, 성능·브라우저 호환성 미검증, 접근성 등급(WCAG) 미확정, 밸런싱 수치 가정.

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
  "coop_over_developer_hours": 80,
  "coop_over_total_hours": 40,
  "base_scope_fits_developer_budget": true,
  "implementation_performed": false,
  "tests_performed": false,
  "command_defense_missing": true,
  "command_investigate_missing": true,
  "turn_bonus_reward_conflict": true
}
```

### Evidence

**1.** board_width=6, board_height=6, max_turns=12: source.goal '기본 보드는 6×6, 한 판은 최대 12턴'.

**2.** developer_hours = 2명 × 20시간/주 × 4주 = 160시간.

**3.** qa_hours = 1명 × 10시간/주 × 4주 = 40시간.

**4.** core_scope_hours = 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성) = 160시간.

**5.** with_coop_hours = 160 + 80(온라인 협동) = 240시간.

**6.** coop_fits_budget=false: 240시간 > 개발 예산 160시간(80시간 초과). 총 가용 200시간 기준으로도 40시간 부족.

**7.** base_scope_fits_developer_budget=true: core 160시간 = 개발 160시간(여유 0).

**8.** seeded_replay_required=true, offline_required=true: source.goal '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '브라우저에서 오프라인으로 동작'.

**9.** Rng 소비 순서 고정: 플레이어 행동→적 행동(정렬 id)→턴 종료.

**10.** SaveCodec version=1, migrate 규칙: 스키마 버전·마이그레이션 요구에 대한 설계 제안.

**11.** 규칙→모듈→테스트 대응: 이동/턴→turn.ts→turn.spec.ts, seed 맵→map.ts/rng.ts→replay.spec.ts, 전투→rules.ts→rules.spec.ts, 저장/복원→codec.ts→save.spec.ts.

**12.** 대응 불일치: 기획의 '방어'·'조사' 행동이 Command 타입에 없음, '턴 추가' 보상이 turn<=12 상한과 충돌 가능.

**13.** 병렬화는 사람 시간 합계를 줄이지 않음(원자료 명시).

**14.** 미확인: 실제 코드 실행·테스트 미수행, 성능·브라우저 호환성 미검증, 접근성 등급(WCAG) 미확정, 밸런싱 수치 가정.
