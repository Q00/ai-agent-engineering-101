# game-design-architecture / baseline / 3회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-baseline/artifacts/game-design-architecture.json)

'유적의 열두 밤' 개발 착수 자료(설계 제안, 실제 구현/테스트 미수행).

[1] 게임 기획서(규칙↔재미 가설)
- 핵심 루프: 매 턴 '탐험(이동/조사) → 조우(전투 또는 함정) → 보상 선택 → 다음 턴'을 반복, 12턴 내 탈출구 도달 시 승리. 재미 가설: 제한 턴(12)과 6×6 협소 보드가 '자원 압박 하의 위험-보상 저울질'을 만들어 매 턴 선택의 무게를 준다.
- 승리: 12턴 이내 탈출 타일에 도달. 패배: HP 0 또는 12턴 소진 시 탈출 미완.
- 난이도: 턴 경과에 따라 조우 확률/적 HP를 단계적으로 상승(초반 완만, 후반 급상승) → '후반 긴장' 가설. 수치는 플레이테스트로 튜닝 필요(미수행).
- 전투: 턴제, 플레이어 행동(공격/방어/도주) 후 적 행동. 명중/피해는 seed RNG로 결정. 도주는 확률적이며 실패 시 피해.
- 보상: 조우 승리 후 3택1(공격력/최대HP/턴 추가 등). 선택은 되돌릴 수 없어 빌드 분기 → '의미 있는 선택' 가설.
- 튜토리얼: 1턴 고정 시나리오(이동→전투→보상)를 스크립트된 seed로 안내.
- 접근성: 키보드 전용 조작, 색맹 대비 팔레트, 텍스트 로그, 애니메이션 감소 옵션, 스크린리더용 상태 텍스트.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain/ (순수 규칙: GameState, Command, Event, 전투/보상/턴 규칙, Rng 인터페이스)
src/application/ (턴 처리, 커맨드 디스패치, SaveCodec)
src/infrastructure/ (SeededRng 구현, Clock 구현, localStorage 저장)
src/presentation/ (Canvas 렌더, 입력 매핑, 접근성 UI)
의존 방향: presentation → application → domain, infrastructure → domain(인터페이스 구현). domain은 렌더/시각/실난수를 import하지 않음(역방향 금지).

[3] 타입/시그니처(짧은 조각)
type Rng = { next(): number; int(maxExclusive: number): number };
type Clock = { now(): number };
type GameState = { seed: number; turn: number; maxTurns: 12; board: {w:6;h:6}; player: {x:number;y:number;hp:number;atk:number}; phase: 'explore'|'combat'|'reward'|'won'|'lost'; rngCursor: number };
type Command = {t:'move';dx:number;dy:number} | {t:'attack'} | {t:'defend'} | {t:'flee'} | {t:'chooseReward';index:number};
type Event = {t:'moved'|'combatStart'|'damage'|'rewardOffered'|'turnEnd'|'win'|'lose'; ...};
function step(s: GameState, c: Command, rng: Rng): {state: GameState; events: Event[]};
interface SaveCodec { encode(s: GameState): string; decode(v: string): GameState; version: number }
턴 처리 의사코드: 입력 검증 → phase별 규칙 적용 → rng 소비(결정적 순서) → 이벤트 생성 → turn 증가 → 승/패 판정.

[4] 규칙→모듈→테스트 대응
이동/턴 → domain/turn.ts → 이동 경계·턴 소진 테스트
seed 맵 → domain/map.ts + SeededRng → 동일 seed 동일 맵 테스트
전투 → domain/combat.ts → 명중/피해 결정성 테스트
보상 → domain/reward.ts → 3택1·중복 방지 테스트
저장/복원 → application/SaveCodec → 저장 후 재현 회귀 테스트
화면 → presentation/render.ts → 스냅샷/접근성 텍스트 테스트
자동 테스트 24h, 접근성 12h.

[5] 저장 스키마 버전·재현·수용 기준
스키마 version=1, 필드: seed, turn, board, player, phase, rngCursor, 선택 이력. 재현 조건: 같은 seed + 같은 입력 순서 → 같은 이벤트 시퀀스. 저장/복원 후 이어서 진행해도 동일 결과. 수용 기준: (a) 12턴 내 승/패 종료, (b) 동일 seed 재현 100%, (c) 저장→복원→재현 일치, (d) 키보드만으로 전 기능 접근.

[6] 일정·제외 범위
개발 2명×20h×4주=160h, QA 1명×10h×4주=40h, 총 200h. 기본 범위 합계 160h(24+24+32+24+20+24+12)로 200h 이내(여유 40h). 협동 포함 시 +80h=240h>200h로 예산 초과. 제외: 서버·로그인·결제·온라인 협동.

[모순·미확인] 협동 포함 시 예산 초과(240>200). QA 40h가 자동 테스트 24h+접근성 12h 검증에 충분한지 미확인. 난이도 곡선 수치 튜닝은 플레이테스트 필요(미수행). 본 문서는 설계 제안이며 실제 구현/테스트는 수행하지 않음.

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

**1.** 개발 가용: 2명×20h×4주=160h; QA 가용: 1명×10h×4주=40h; 총 200h

**2.** 기본 범위: 24+24+32+24+20+24+12=160h ≤ 200h (여유 40h)

**3.** 협동 포함: 160+80=240h > 200h → 예산 초과

**4.** 보드 6×6, 최대 12턴은 source goal에 명시

**5.** seed+입력 순서 동일 시 동일 결과, 저장/복원 재현은 source goal 요구사항

**6.** 서버·로그인·결제·온라인 협동은 기본 범위 제외(source goal)

**7.** 실제 구현/테스트 미수행: 본 산출물은 설계 제안 텍스트
