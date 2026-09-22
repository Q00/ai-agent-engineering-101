# game-design-architecture / baseline / 3회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260921T113158-suite-ab4e67-r3-game-design-architecture-baseline/artifacts/game-design-architecture.json)

'유적의 열두 밤' 개발 착수 자료(설계 제안, 실제 구현/테스트 미수행).

[1] 게임 기획서(규칙↔재미 가설)
- 보드 6×6, 최대 12턴. 루프: 탐험(이동/조사)→전투→보상 선택→탈출. 각 턴은 플레이어 1행동 후 세계 1반응.
- 승리: 탈출구 도달 후 탈출 명령 성공. 패배: 체력 0 또는 12턴 소진 시 미탈출.
- 난이도 곡선: 턴 1-4 저위험(탐험 보상), 5-8 중위험(전투 빈도↑), 9-12 고위험(엘리트/자원 압박). 가설: '제한 턴+자원 희소성'이 위험-보상 긴장을 만든다.
- 전투: 턴제, 공격/방어/아이템. 명중·피해는 seed RNG로 결정. 가설: 결정적 RNG가 재현성과 학습 가능한 전략을 동시에 제공.
- 보상: 전투/탐험 후 3택1(공격↑/체력↑/유틸). 가설: 선택지 제한이 빌드 다양성과 후회 없는 결정을 유도.
- 튜토리얼: 1턴 스크립트 안내(이동→전투→보상). 접근성: 키보드 전용 조작, 색맹 대비 팔레트+아이콘, 텍스트 로그, 포커스 표시.

[2] 디렉터리 트리·모듈 책임·의존 방향
src/domain(순수 규칙: state, combat, map, rewards) ← src/application(턴 처리, command 해석) ← src/infrastructure(Rng, Clock, SaveCodec 구현) ← src/ui(Canvas 렌더, 입력). 의존은 단방향: ui→application→domain, infrastructure는 인터페이스 구현으로 주입. domain은 렌더/IO를 모른다.

[3] 타입/시그니처(짧은 조각)
type GameState={seed:number;turn:number;hp:number;pos:{x:number;y:number};board:Cell[][];log:Event[];version:'v1'};
type Command={type:'MOVE'|'ATTACK'|'DEFEND'|'ITEM'|'ESCAPE';dir?:'N'|'S'|'E'|'W';itemId?:string};
type Event={turn:number;kind:string;payload:Record<string,unknown>};
interface Rng{nextInt(maxExclusive:number):number;}
interface Clock{now():number;}
interface SaveCodec{encode(s:GameState):string;decode(raw:string):GameState;}
function step(s:GameState,cmd:Command,rng:Rng):{state:GameState;events:Event[]};
의사코드: validate(cmd)→apply domain rule→rng로 결과 결정→events 생성→turn++→승/패 판정.

[4] 규칙→모듈→테스트 대응
이동/턴→domain/map+application/turn→턴 소모·경계 테스트. seed 맵→domain/map+Rng→동일 seed 동일 맵. 전투→domain/combat→명중/피해 결정성. 저장/복원→SaveCodec→round-trip 동일. 화면→ui→스냅샷(미검증). 접근성→ui→수동 점검(미검증).

[5] 저장 스키마 v1·재현 테스트·수용 기준
스키마: {version:'v1',seed,turn,hp,pos,board,log}. 재현: 같은 seed+같은 Command 순서→동일 Event 로그; 저장→복원 후 이어서 같은 입력→동일 결과. 수용: 결정적 재현 통과, round-trip 통과, 12턴/체력0 종료 정확.

[6] 일정·제외 범위
4주, 개발 2명×20h/주=160h, QA 1명×10h/주=40h. 병렬화 지점: 맵/전투/저장/화면 병렬, QA는 자동 테스트·접근성 병행. 제외: 서버·로그인·결제·온라인 협동.

[모순·미확인] 전투 명중 공식·보상 확률 수치 미정의(설계 제안 단계). '탈출' 판정이 턴 소모 여부 불명확. 예산: 기본 범위 160h=개발 예산 160h로 여유 0h(리스크), 협동 포함 240h는 80h 초과. 실제 코드 실행/테스트는 수행하지 않음(미검증).

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
  "core_fits_budget": true,
  "core_budget_margin_hours": 0,
  "coop_over_budget_hours": 80,
  "implementation_performed": false,
  "tests_executed": false
}
```

### Evidence

**1.** 개발 예산: 2명×20h×4주=160h. QA 예산: 1명×10h×4주=40h.

**2.** 기본 범위 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성)=160h.

**3.** 협동 포함: 160+80=240h. 240-160=80h 초과 → coop_fits_budget=false.

**4.** 기본 범위 160h = 개발 예산 160h, 여유 0h → core_fits_budget=true, core_budget_margin_hours=0.

**5.** 재현 요구: 같은 seed+같은 입력 순서→동일 결과, 저장/복원 후 재현 → seeded_replay_required=true.

**6.** 오프라인 브라우저 동작 요구, 서버·로그인·결제·온라인 협동 제외 → offline_required=true.

**7.** 설계 문서·코드 조각은 summary에 텍스트로 제시했고, 실제 구현/테스트는 수행하지 않아 implementation_performed=false, tests_executed=false로 명시.
