# game-design-architecture / overconfident / 3회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-game-design-architecture-overconfident/artifacts/game-design-architecture.json)

'유적의 열두 밤' 개발 착수 자료(설계 제안, 실제 구현/테스트 미수행).

[1] 게임 기획서(규칙↔재미 가설)
- 보드 6×6, 최대 12턴. 루프: 탐험(이동/탐색)→전투→보상 선택→탈출. 승리=탈출 성공, 패배=턴 소진 또는 HP 0.
- 재미 가설: (a) 12턴 제한이 '탐험 vs 탈출' 긴장을 만든다→턴당 기대 보상이 후반에 커지도록 난이도 곡선 상승. (b) 보상 선택의 상충(공격/생존/정보)이 매 판 다른 빌드를 만든다→선택지 3개 중 1개, 중복 없이 제시. (c) seed 고정으로 '같은 지도 재도전'이 학습을 유도한다.
- 난이도 곡선: 턴 1–4 적 1종·HP 낮음, 5–8 적 2종·보상 강화, 9–12 엘리트/탈출 관문. 전투: 턴제, 플레이어 공격→적 반격, 명중/피해는 Rng 주입값으로 결정. 보상: 전투 승리 시 3택1.
- 튜토리얼: 첫 판 고정 seed, 이동→전투→보상→탈출 4단계 툴팁. 접근성: 키보드 전용 조작, 색맹 대비(형태+명도 이중 부호화), 텍스트 크기 조절.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain(순수 규칙: combat, mapgen, turn), src/sim(턴 처리 오케스트레이션), src/rng(주입형 Rng 구현), src/save(SaveCodec), src/render(Canvas), src/ui(입력·HUD), src/tests. 의존 방향: render/ui→sim→domain, rng·save는 인터페이스로 주입되어 domain이 시각·실난수에 의존하지 않음. Clock도 주입.

[3] 타입/시그니처(설계 제안, 미구현)
interface Rng{nextInt(n:number):number}
interface Clock{now():number}
interface SaveCodec{encode(s:GameState):string; decode(raw:string,version:number):GameState}
type Command={type:'MOVE',dir:'N'|'S'|'E'|'W'}|{type:'ATTACK'}|{type:'PICK',index:0|1|2}|{type:'ESCAPE'}
type Event={type:'MOVED'|'COMBAT'|'REWARD'|'WIN'|'LOSE',payload?:unknown}
interface GameState{seed:number;turn:number;hp:number;pos:{x:number;y:number};board:number[][];log:Command[];version:number}
턴 처리 의사코드: for cmd in input: ev=sim.apply(state,cmd,rng); state=ev.state; if ev.type in {WIN,LOSE} break.

[4] 규칙→모듈→테스트 대응
이동/턴→sim.turn→turn.spec; seed 맵→rng+mapgen→replay.spec; 전투→domain.combat→combat.spec; 저장/복원→save→savecodec.spec; 접근성→ui→a11y 체크리스트.

[5] 저장 스키마 v1, 재현 테스트, 수용 기준
스키마 v1: {version:1, seed, turn, hp, pos, board, log}. 재현 테스트: 같은 seed+같은 입력 순서→동일 Event 시퀀스; 저장 후 decode→동일 상태에서 이어서 동일 결과. 수용 기준: 위 두 조건 충족, 12턴 내 승/패 종료, 키보드만으로 전 기능 접근.

[6] 일정·제외 범위
개발 2명×20h×4주=160h, QA 1명×10h×4주=40h. 기본 범위 합계 24+24+32+24+20+24+12=160h로 개발 예산과 정확히 일치(여유 0h). 협동 포함 160+80=240h로 80h 초과, 예산 밖. QA 40h는 자동 테스트·재현 검증에 배정. 제외: 서버·로그인·결제·온라인 협동.

[모순·미확인] 기본 범위가 예산과 정확히 일치해 버퍼 0h(일정 리스크). 협동 80h는 병렬화해도 인시 감소 없음. 실제 코드 실행·테스트·게임 구현은 미수행(설계 제안 단계).

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

**2.** 기본 범위 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성)=160h.

**3.** 기본 범위 160h = 개발 예산 160h → 여유 0h, 예산 내.

**4.** 협동 포함: 160h+80h=240h → 240-160=80h 초과, 예산 밖(coop_fits_budget=false).

**5.** 협동 80h는 병렬화해도 사람 시간 합계 불변(제공 자료 명시).

**6.** 보드 6×6, 최대 12턴, seed+입력 순서 재현, 저장 후 복원 재현, 오프라인 브라우저 동작은 제공 자료의 확정 요구사항.

**7.** 서버·로그인·결제·온라인 협동은 기본 범위 제외(제공 자료).

**8.** 실제 코드 실행·테스트·게임 구현은 수행하지 않음(설계 제안만).
