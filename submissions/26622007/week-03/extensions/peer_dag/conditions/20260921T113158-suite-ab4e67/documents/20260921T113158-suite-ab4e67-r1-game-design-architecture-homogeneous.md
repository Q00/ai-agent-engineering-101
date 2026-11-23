# game-design-architecture / homogeneous / 1회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r1-game-design-architecture-homogeneous/artifacts/game-design-architecture.json)

'유적의 열두 밤' 개발 착수 자료(설계 제안, 실제 구현/테스트 미수행).

[1] 게임 기획서(규칙↔재미 가설)
- 핵심 루프: 매 턴 '탐험(이동/조사) → 조우(전투 또는 함정) → 보상 선택' 중 하나를 수행. 12턴 내 탈출구 도달 시 승리, HP 0 또는 12턴 소진 시 패배.
- 보드: 6×6(36칸), 시작칸 1, 탈출구 1, 나머지에 적/보상/함정/빈칸 배치. seed로 배치 결정.
- 재미 가설: (a) 짧은 12턴 제한이 '자원 배분 긴장'을 만든다 → 턴당 1행동 규칙으로 지원. (b) seed 고정으로 '같은 지도 재도전' 학습 재미 → Rng 주입으로 지원. (c) 보상 선택의 트레이드오프(공격↑ vs HP↑)가 빌드 다양성 → 보상 2택1 규칙으로 지원.
- 승리/패배: 승리=탈출구 도달, 패배=HP≤0 또는 turn>12.
- 난이도: 적 HP/공격력을 seed 파생 난이도 계수(1.0/1.25/1.5)로 스케일. (수치 곡선은 미검증)
- 전투: 턴제, 플레이어 공격→적 반격. 데미지=base+보너스, 최소 1. 명중 판정은 Rng 사용.
- 보상: 전투/조사 후 2개 중 1개 선택(공격+1, 최대HP+2, 회복+3 등).
- 튜토리얼: 1턴에 이동/공격/보상 각 1회 강제 안내 오버레이.
- 접근성: 키보드 전용 조작, 색맹 대비 팔레트, 텍스트 로그, 애니메이션 감소 옵션.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain/(순수 규칙, 외부 의존 없음): state.ts, commands.ts, events.ts, rules/(combat, movement, reward, turn), rng.ts(인터페이스), saveCodec.ts(인터페이스)
src/application/(유스케이스): gameLoop.ts, turnProcessor.ts
src/infra/(구현): seededRng.ts, systemClock.ts, localStorageSave.ts
src/ui/(Canvas 렌더/입력): renderer.ts, input.ts, main.ts
의존 방향: ui→application→domain, infra→domain(인터페이스 구현). domain은 어떤 외부 모듈도 import하지 않음.

[3] 타입/시그니처(설계 제안, 미컴파일)
type Vec={x:number;y:number};
type Tile='empty'|'enemy'|'reward'|'trap'|'exit';
interface GameState{turn:number;maxTurns:12;board:6x6 Tile;player:{pos:Vec;hp:number;maxHp:number;atk:number};enemies:Enemy[];seed:string;schemaVersion:number;status:'playing'|'won'|'lost';}
type Command={type:'move';dir:'up'|'down'|'left'|'right'}|{type:'attack';targetId:string}|{type:'chooseReward';rewardId:string};
type Event={type:'moved'|'attacked'|'damaged'|'rewarded'|'won'|'lost';payload:Record<string,unknown>};
interface Rng{nextInt(maxExclusive:number):number;fork(label:string):Rng;}
interface Clock{now():number;}
interface SaveCodec{encode(s:GameState):string;decode(raw:string):GameState;version:number;}
function processTurn(state:GameState,cmd:Command,rng:Rng):{state:GameState;events:Event[]};
의사코드: validate(cmd)→apply(cmd)→resolveCombat(rng)→checkEnd()→turn++→return.
Rng/Clock은 주입, 렌더러는 GameState만 읽고 도메인을 변경하지 않음.

[4] 규칙→모듈→테스트 대응표
이동/턴→rules/movement,turn→movement.test,turnLimit.test
seed 맵→infra/seededRng→replay.test(동일 seed 동일 결과)
전투→rules/combat→combat.test(데미지/명중)
저장/복원→saveCodec→saveRoundtrip.test
화면→ui/renderer→(수동 검증)
접근성→ui/input,renderer→(수동 검증)

[5] 저장 스키마·재현 테스트·수용 기준
schemaVersion=1, 필드: seed, turn, board, player, enemies, status. decode 시 버전 불일치면 마이그레이션 또는 거부.
재현 테스트: 동일 seed+동일 Command 시퀀스 → 동일 Event 시퀀스. 저장→로드 후 이어서 진행해도 동일.
수용 기준: 12턴 내 승/패 종료, seed 재현 일치, 저장 라운드트립 동등, 키보드만으로 전 조작 가능.

[6] 일정·제외 범위
개발 2명×20h×4주=160h, QA 1명×10h×4주=40h.
기본 범위 합계=24+24+32+24+20+24+12=160h → 개발 예산과 정확히 일치(버퍼 0).
협동 포함=160+80=240h → 160h 초과.
제외: 서버·로그인·결제·온라인 협동.

[모순·미확인] 기본 범위가 예산과 정확히 일치해 버퍼 0(리스크), QA 40h 대비 자동 테스트 24h+수동 검증 부담 과다 가능, 협동 제외 확정 필요, 난이도 곡선 수치 미검증, 실제 코드 컴파일/실행/테스트 미수행.

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

**1.** 개발 가용=2명×20h×4주=160h; QA 가용=1명×10h×4주=40h

**2.** 기본 범위=24(이동/턴)+24(seed맵)+32(전투)+24(저장/복원)+20(화면)+24(자동테스트)+12(접근성)=160h

**3.** 협동 포함=160+80=240h; 240>160이므로 coop_fits_budget=false

**4.** 기본 범위 160h=개발 예산 160h로 여유 0

**5.** 보드 6×6, 최대 12턴은 source goal에 명시

**6.** seed+입력 순서 동일 시 동일 결과, 저장 후 재현 요구는 source goal에 명시

**7.** 서버·로그인·결제·온라인 협동은 기본 범위 제외(source goal)

**8.** 설계 문서/코드 조각은 제안이며 실제 구현·컴파일·테스트는 미수행
