# game-design-architecture / homogeneous / 3회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-game-design-architecture-homogeneous/artifacts/game-design-architecture.json)

'유적의 열두 밤' 개발 착수 자료(설계 제안, 실제 구현/테스트 미수행).

[1] 게임 기획서(규칙↔재미 가설)
- 보드 6×6, 최대 12턴. 매 턴 플레이어는 1개 Command(이동/탐험/전투/보상선택/탈출시도)를 제출하고, 도메인이 Event 목록을 생성한다.
- 핵심 루프: 탐험(미탐색 타일 공개, seed 기반 배치) → 전투(조우 시 턴 소모 교전) → 보상(전투 승리 후 3택1) → 탈출(출구 도달 시 탈출 시도).
- 승리: 12턴 이내 출구에서 탈출 성공. 패배: 체력 0 또는 12턴 소진 시 미탈출.
- 난이도 곡선: 턴 경과에 따라 조우 확률·적 위협 상승(초반 완만, 후반 급상승)으로 '탈출 vs 더 탐험' 긴장 유도.
- 전투 규칙(제안): 공격/방어/회피 선택, 피해=기본공격력-방어, 최소 1. 적 체력 0 시 승리.
- 보상 규칙(제안): 체력 회복/공격 강화/방어 강화 중 1택, 중복 시 스택.
- 튜토리얼: 1턴차에 이동·탐험 안내 오버레이, 첫 조우에서 전투 선택지 설명. 접근성: 키보드 전용 조작, 색맹 대비 아이콘+텍스트 병기, 텍스트 크기 옵션, 애니메이션 감소 옵션.
- 재미 가설: (a) 제한 턴+랜덤 배치→매판 다른 경로 결정의 긴장, (b) 보상 3택1→빌드 선택의 자기결정감, (c) seed 재현→공략 공유/재도전 동기.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain (순수 규칙: 상태 전이, 전투/보상/턴, Rng 사용) ← src/app (유스케이스: Command 적용, Event 생성, 저장/복원 오케스트레이션) ← src/render (Canvas 그리기, 입력→Command 변환) ; src/platform (Rng/Clock/SaveCodec 구현체, 브라우저 API 어댑터)는 인터페이스만 domain/app에 제공. tests/는 domain 우선 단위 테스트. 의존 방향: render→app→domain, platform→(인터페이스)domain/app. domain은 render/platform을 import하지 않음.

[3] TypeScript 타입/시그니처(설계 조각, 미실행)
type Vec={x:number;y:number}; type TileKind='floor'|'wall'|'exit'|'enemy'|'reward';
interface GameState{version:'v1';seed:number;turn:number;maxTurns:12;board:{w:6;h:6;tiles:TileKind[]};player:{pos:Vec;hp:number;atk:number;def:number};rngState:number;status:'playing'|'won'|'lost';}
type Command={type:'move';dir:'up'|'down'|'left'|'right'}|{type:'explore'}|{type:'attack'}|{type:'defend'}|{type:'chooseReward';index:0|1|2}|{type:'tryEscape'};
type Event={type:'moved'|'explored'|'combat'|'rewardOffered'|'rewardChosen'|'escaped'|'defeated'|'turnEnded';payload?:Record<string,unknown>};
interface Rng{nextInt(maxExclusive:number):number;getState():number;setState(s:number):void;}
interface Clock{now():number;}
interface SaveCodec{encode(s:GameState):string;decode(text:string):GameState;}
턴 처리 의사코드: applyCommand(state,cmd,rng): if status!=='playing' return []; events=[]; switch cmd.type: move→위치 갱신, 타일 효과; explore→seed 기반 인접 공개; attack/defend→전투 해소; chooseReward→스탯 적용; tryEscape→출구 여부 검사 후 won. 이후 turn+=1, turn>maxTurns && status==='playing'→lost. rngState 저장. return events.

[4] 규칙→모듈→테스트 대응표
- 이동/턴(24h)→domain/turn.ts→tests/turn.spec(턴 증가, 12턴 패배)
- seed 맵(24h)→domain/map.ts→tests/map.spec(동일 seed 동일 배치)
- 전투(32h)→domain/combat.ts→tests/combat.spec(피해 최소1, 승패)
- 저장/복원(24h)→app/save.ts+platform/saveCodec→tests/save.spec(encode/decode 왕복, 재현)
- 화면(20h)→render/*→수동/스냅샷(미검증)
- 자동 테스트(24h)→tests/*→CI 가정(미검증)
- 접근성(12h)→render/a11y→수동 점검(미검증)

[5] 저장 스키마 v1·재현 테스트·수용 기준
- 스키마: {version:'v1', seed, rngState, turn, board, player, status}. 버전 불일치 시 마이그레이션 또는 거부.
- 재현 테스트(설계): 동일 seed+동일 Command 시퀀스→동일 Event 시퀀스 및 최종 GameState. 저장→로드 후 이어서 동일 시퀀스→동일 결과.
- 수용 기준: 6×6/12턴 준수, 승패 조건 명확, seed 재현, 저장/복원 재현, 오프라인 동작, 제외 범위 미포함.

[6] 일정·제외 범위
- 개발 2명×20h×4주=160h, QA 1명×10h×4주=40h.
- 기본 범위 합계=24+24+32+24+20+24+12=160h → 개발 예산 160h와 정확히 일치(여유 0h).
- 협동 포함=160+80=240h → 160h 예산 대비 80h 초과, coop_fits_budget=false.
- 제외: 서버·로그인·결제·온라인 협동.

[모순/미확인]
- 모순: 기본 범위가 예산과 정확히 일치해 버퍼 0h → 일정 리스크. QA 40h는 별도 인력이라 개발 160h와 합산 여부 해석 필요(여기선 개발 예산만 비교).
- 미확인: 전투/보상 세부 수치 밸런스, 접근성·화면·자동테스트의 실제 검증은 미수행(설계 제안 단계). 코드 실행/게임 구현 완료 아님.

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

**1.** 개발 예산: 2명×20h×4주=160h. QA 예산: 1명×10h×4주=40h.

**2.** 기본 범위 합계: 24+24+32+24+20+24+12=160h (이동/턴24, seed맵24, 전투32, 저장/복원24, 화면20, 자동테스트24, 접근성12).

**3.** 협동 포함: 160+80=240h. 240>160이므로 coop_fits_budget=false. 병렬화해도 사람 시간 합계는 80h 증가.

**4.** 보드 6×6, 최대 12턴은 source goal의 확정 수치.

**5.** seed 재현·저장 후 재현 요구는 source goal의 명시 조건 → seeded_replay_required=true.

**6.** 오프라인 브라우저 동작 요구 → offline_required=true.

**7.** 제외 범위(서버·로그인·결제·온라인 협동)는 source goal 명시.

**8.** 설계 문서/코드 조각은 제안이며 실제 구현·테스트 실행은 미수행.
