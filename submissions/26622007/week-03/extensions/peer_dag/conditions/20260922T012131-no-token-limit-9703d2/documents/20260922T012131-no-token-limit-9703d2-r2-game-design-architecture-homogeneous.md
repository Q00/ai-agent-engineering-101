# game-design-architecture / homogeneous / 2회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-game-design-architecture-homogeneous/artifacts/game-design-architecture.json)

설계 착수 자료(구현/테스트 미수행, 설계 제안).

[1] 게임 기획서 요약 — '유적의 열두 밤'
- 보드 6×6, 최대 12턴, 1인용 오프라인 2D 턴제 로그라이크.
- 핵심 루프: 탐험(미공개 타일 공개/자원 획득) → 전투(적 조우 시 턴제 교전) → 보상(전투/탐험 후 3택1) → 탈출(출구 도달 시 승리).
- 승리: 12턴 이내 출구 타일에서 탈출. 패배: 체력 0 또는 12턴 소진 시 미탈출.
- 난이도 곡선: 턴 1–4 적 1종·저위협, 5–8 적 2종·보상 강화, 9–12 적 3종·엘리트/함정 증가. 재미 가설: '자원 압박 속 탈출 결정'이 긴장과 성취를 만든다.
- 전투 규칙: 플레이어 행동(공격/방어/도주) → 적 행동 순. 공격력=기본+보상 보정, 방어는 다음 피해 50% 감소. 도주는 seed 기반 확률.
- 보상 규칙: 3택1(공격+, 최대체력+, 이동+, 회복). 중복 선택 시 스택, 상한 존재.
- 튜토리얼: 첫 판 고정 seed로 이동→전투→보상→탈출 4단계 안내. 접근성: 키보드 전용 조작, 색맹 대비 팔레트, 텍스트 로그, 애니메이션 감속/끄기 옵션.

[2] 아키텍처(디렉터리 트리·의존 방향)
src/domain (순수 규칙: state, turn, combat, map, rng)
src/app (유스케이스: newGame, applyCommand, save/load)
src/render (Canvas 렌더러, 입력→Command 변환)
src/platform (Rng/Clock/SaveCodec 실제 구현, 브라우저 어댑터)
tests (단위/재현/스키마)
의존 방향: render → app → domain, platform은 인터페이스로 주입(domain은 platform을 모름).

[3] TypeScript 타입/시그니처(설계 제안, 짧은 조각)
type Rng = { nextInt(maxExclusive:number):number };
type Clock = { now():number };
type GameState = { seed:number; turn:number; board:Cell[][]; player:Player; enemies:Enemy[]; phase:'explore'|'combat'|'reward'|'end'; rngCursor:number };
type Command = {type:'move',dir:Dir}|{type:'attack'}|{type:'defend'}|{type:'flee'}|{type:'pickReward',index:number};
type Event = {type:'moved'|'damaged'|'rewardOffered'|'won'|'lost', payload:unknown};
type SaveCodec = { encode(s:GameState):string; decode(v:string):GameState };
턴 처리 의사코드: applyCommand(state,cmd,rng) → validate → mutate copy → emit Events → advance turn/phase → return {state,events}.

[4] 규칙→모듈→테스트 대응표
- 이동/턴 → domain/turn.ts → turn.spec(턴 소진, 승패)
- seed 맵 → domain/map.ts → map.spec(동일 seed 동일 보드)
- 전투 → domain/combat.ts → combat.spec(피해/방어/도주)
- 저장/복원 → app/save.ts + platform/saveCodec.ts → save.spec(라운드트립)
- 화면 → render/canvas.ts → render.spec(스냅샷)
- 자동 테스트 → tests/* → 재현 테스트(seed+입력 순서)
- 접근성 → render/a11y.ts → a11y.spec(키보드/대비)

[5] 저장 스키마·재현·수용 기준
- 스키마 버전 v1, 필드: version, seed, turn, board, player, enemies, phase, rngCursor.
- 재현 테스트: 동일 seed+동일 Command 시퀀스 → 동일 Event/State 해시. 저장→로드 후 이어서 동일 결과.
- 수용 기준: 12턴 내 승/패 판정, 라운드트립 동일성, seed 재현성, 키보드 전용 완주.

[6] 일정(4주)과 제외 범위
- W1: 이동/턴(24h)+seed 맵(24h) 착수, QA 테스트 골격.
- W2: 전투(32h), 저장/복원(24h) 착수.
- W3: 화면(20h), 자동 테스트(24h).
- W4: 접근성(12h), 통합·버그픽스, QA 검증.
- 제외: 서버·로그인·결제·온라인 협동.

[모순·미확인]
- 기본 범위 160h가 개발 예산 160h와 정확히 일치 → 버퍼 0, 리스크 높음.
- QA 40h가 자동 테스트 24h+접근성 12h 검증에 충분한지 미확인.
- 협동 제외 시 재현성 요구와 무관함은 확인됨(단일 클라이언트).
- 실제 구현/테스트는 미수행(설계 제안).

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

**1.** 개발 예산: 개발자 2명 × 20h/주 × 4주 = 160h

**2.** QA 예산: QA 1명 × 10h/주 × 4주 = 40h

**3.** 기본 범위 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성) = 160h

**4.** 기본 범위 160h = 개발 예산 160h → 여유 0h

**5.** 협동 포함: 160h + 80h = 240h > 160h → coop_fits_budget=false

**6.** 보드/턴: source에 '기본 보드는 6×6, 한 판은 최대 12턴'으로 명시

**7.** 재현성: source에 '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과' 및 '저장 후 불러와도 재현' 요구

**8.** 오프라인: source에 '브라우저에서 오프라인으로 동작하는 1인용' 및 '서버·로그인·결제·온라인 협동은 기본 범위에 없다' 명시

**9.** 설계 문서/코드 조각은 summary에 텍스트로 제시했고 실제 구현·테스트는 수행하지 않음(미검증)
