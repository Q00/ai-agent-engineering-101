# game-design-architecture / homogeneous / 3회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-homogeneous/artifacts/game-design-architecture.json)

세 선행 산출물(budget_facts, game_design, code_architecture)을 통합해 '유적의 열두 밤' 개발 착수 자료를 완성했다. (1) 예산: 개발자 2명×20h×4주=160h, QA 1명×10h×4주=40h. 기본 범위 24+24+32+24+20+24+12=160h로 개발 예산과 정확히 일치(적합, 여유 0h). 협동 포함 160+80=240h로 80h 초과(부적합). (2) 게임 기획: 6×6 보드(36칸), 최대 12턴, 오프라인 1인용. 핵심 루프는 탐험→전투→보상→탈출 4단계, 난이도 3구간(1~3/4~8/9~12), 전투 최대 3라운드, 보상 3지선다, 튜토리얼 3턴, 접근성(WCAG AA, 키보드, aria-live). 각 규칙에 재미 가설 연결. (3) 코드 구조: src/domain(순수 규칙)→없음, src/app→domain, src/infra→domain(인터페이스 구현), src/render→app/domain 읽기 전용. Rng/Clock/SaveCodec은 도메인 인터페이스로 정의하고 infra에서 주입. (4) 규칙→모듈→테스트 대응표: 탐험/이동·턴→domain/reducer.ts+app/turnLoop.ts→tests/domain; seed 맵→domain/rng.ts+infra/rngImpl.ts→tests/replay; 전투→domain/reducer.ts→tests/domain; 보상→domain/commands.ts+reducer.ts→tests/domain; 저장/복원→domain/save.ts+infra/saveCodecImpl.ts→tests/save; 화면/접근성→render/canvasAdapter.ts+viewModel.ts→수동 점검. (5) 턴 처리 의사코드: Command 수집→검증→reducer→Event→Rng 소비 순서 고정→SaveCodec 스냅샷. 난수는 reducer 내 결정적 순서로만 소비하고 render/입력 수집 단계에서는 Rng 미호출. 저장 스키마에 version 필드 포함, seed+command log replay 및 save→load→replay 비교로 재현 검증. (6) 일정: 4주, 개발 160h·QA 40h, 여유 0h. 제외 범위: 서버·로그인·결제·온라인 협동(협동 시 +80h 초과). 모순/미확인: 기본 범위 버퍼 0h의 일정 리스크, 협동 예산 초과, 컴파일·실행·테스트 미수행, 애니메이션 타이밍·입력 큐 정책·저장 마이그레이션 정책 미확정. 본 문서는 설계 제안이며 실제 구현/테스트는 수행하지 않았다.

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
  "developer_count": 2,
  "developer_weekly_hours_each": 20,
  "qa_count": 1,
  "qa_weekly_hours": 10,
  "weeks": 4,
  "core_loop_stages": 4,
  "difficulty_phases": 3,
  "combat_max_rounds": 3,
  "reward_choices": 3,
  "tutorial_turns": 3,
  "excluded_server": true,
  "excluded_login": true,
  "excluded_payment": true,
  "excluded_online_coop": true,
  "rng_injection_required": true,
  "clock_injection_required": true,
  "render_readonly": true,
  "save_schema_version_field": true,
  "implementation_done": false,
  "tests_executed": false,
  "animation_timing_confirmed": false,
  "input_queue_policy_confirmed": false,
  "save_migration_policy_confirmed": false
}
```

### Evidence

**1.** 개발자 가용: 2명 × 20시간/주 × 4주 = 160시간. QA 가용: 1명 × 10시간/주 × 4주 = 40시간.

**2.** 기본 범위 합산: 이동/턴 24 + seed 기반 맵 24 + 전투 32 + 저장/복원 24 + 화면 20 + 자동 테스트 24 + 접근성 12 = 160시간. 개발 예산 160시간과 동일 → core_fits_budget=true, core_budget_margin_hours=0.

**3.** 협동 포함: 160 + 80 = 240시간. 240 - 160 = 80시간 초과 → coop_fits_budget=false, coop_over_budget_hours=80. 병렬화해도 사람 시간 합계 불변(자료 명시).

**4.** 고정 사실: 보드 6×6(board_width=6, board_height=6), 최대 12턴(max_turns=12), 같은 seed·같은 입력 순서 재현 필수(seeded_replay_required=true), 오프라인 동작 필수(offline_required=true).

**5.** 게임 기획: 핵심 루프 4단계(탐험→전투→보상→탈출), 난이도 3구간(1~3, 4~8, 9~12), 전투 최대 3라운드, 보상 3지선다, 튜토리얼 3턴, 접근성 WCAG AA·키보드·aria-live.

**6.** 코드 구조: domain(순수 규칙)→없음, app→domain, infra→domain(인터페이스 구현), render→app/domain 읽기 전용. Rng/Clock/SaveCodec은 도메인 인터페이스로 정의하고 infra에서 주입.

**7.** 타입 시그니처(설계 조각): type Rng={nextInt(maxExclusive:number):number;nextFloat():number;getState():RngState;}; type Clock={now():number;}; interface SaveCodec{encode(s:SaveData):string;decode(raw:string):SaveData;} type Command={type:'move',dir:Dir}|{type:'attack',targetId:string}|{type:'chooseReward',rewardId:string}|{type:'endTurn'}; type GameState={version:number;seed:number;turn:number;board:{w:number;h:number};player:PlayerState;rngState:RngState;status:'playing'|'won'|'lost'};

**8.** 턴 처리 의사코드: (1)commands=collectInput(); (2)for cmd: if !validate(state,cmd) continue; (3)state=reduce(state,cmd,rng,clock) — Rng 소비 지점 고정; (4)events=emit(state,cmd); (5)if turnEnded: state.turn+=1; (6)snapshot=saveCodec.encode(toSaveData(state)).

**9.** 재현 설계: seed+command log replay로 Event/State 해시 비교, save→load→replay 비교, 저장 스키마에 version 필드 포함.

**10.** 미검증: 실제 컴파일·실행·자동 테스트 미수행(implementation_done=false, tests_executed=false). 애니메이션 타이밍·입력 큐 정책·저장 마이그레이션 정책은 설계 제안이며 확정 아님.

**11.** 모순/리스크: 기본 범위 160시간이 개발 예산과 정확히 일치해 버퍼 0시간으로 일정 리스크가 큼. 협동 포함 시 예산 초과로 기본 범위에서 제외해야 함.

## game-design-architecture/budget_facts.json

Worker: B; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-homogeneous/artifacts/game-design-architecture/budget_facts.json)

제공된 추정치로 개발자/QA 가용 시간과 기본·협동 포함 범위 총사람시간을 계산하고 예산 적합 여부를 판정했다. 개발자 가용 160시간, QA 가용 40시간. 기본 범위 합계는 24+24+32+24+20+24+12=160시간으로 개발자 가용 160시간과 정확히 일치하여 적합(예산 소진, 여유 0). 협동 포함 범위는 160+80=240시간으로 개발자 가용 160시간을 80시간 초과하여 부적합. 고정 사실(보드 6×6, 최대 12턴, seed 재현 필수, 오프라인 필수)도 확정했다.

### Facts

```json
{
  "developer_hours": 160,
  "qa_hours": 40,
  "core_scope_hours": 160,
  "with_coop_hours": 240,
  "coop_fits_budget": false,
  "seeded_replay_required": true,
  "offline_required": true,
  "board_width": 6,
  "board_height": 6,
  "max_turns": 12,
  "core_fits_budget": true,
  "core_budget_margin_hours": 0,
  "coop_over_budget_hours": 80,
  "total_scope_items": 7
}
```

### Evidence

**1.** 개발자 가용 시간: 2명 × 20시간/주 × 4주 = 160시간

**2.** QA 가용 시간: 1명 × 10시간/주 × 4주 = 40시간

**3.** 기본 범위 합산: 이동/턴 24 + seed 기반 맵 24 + 전투 32 + 저장/복원 24 + 화면 20 + 자동 테스트 24 + 접근성 12 = 160시간

**4.** 협동 포함 범위: 기본 160 + 온라인 협동 80 = 240시간 (병렬화해도 사람 시간 합계 불변)

**5.** 예산 판정: 기본 160 ≤ 개발자 160 → 적합(여유 0시간), 협동 240 > 160 → 부적합, 초과분 240-160=80시간

**6.** 고정 사실: 보드 6×6(board_width=6, board_height=6), 최대 12턴(max_turns=12), 같은 seed·같은 입력 순서 재현 필수(seeded_replay_required=true), 오프라인 동작 필수(offline_required=true)

**7.** QA 40시간은 기본 범위 160시간 대비 약 25% 수준으로 자동 테스트 24시간 항목과 별도로 QA 검증 여력을 제공함

## game-design-architecture/code_architecture.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-homogeneous/artifacts/game-design-architecture/code_architecture.json)

TypeScript/HTML Canvas 코드 구조 설계 제안(설계 수준, 실제 구현/테스트 미수행). 디렉터리는 src/domain(순수 규칙), src/app(유스케이스·턴 루프), src/infra(Rng/Clock/SaveCodec 구현), src/render(읽기 전용 뷰), tests로 나눈다. 의존 방향: domain→없음, app→domain, infra→domain(인터페이스 구현), render→app/domain 읽기 전용. Rng/Clock은 도메인 인터페이스로 정의하고 infra에서 주입한다. 턴 처리는 Command 수집→검증→reducer→Event 생성→Rng 소비 순서 고정→SaveCodec 스냅샷 순으로, 난수 소비 지점을 명시해 같은 seed+입력 순서면 동일 Event/State가 나오게 한다. 저장 스키마에 version 필드를 두고 seed+command log replay 및 save→load→replay 비교로 재현을 검증한다. render는 GameState/Event를 읽기만 하며 Canvas 어댑터는 순수 뷰 변환만 담당한다. 아래 코드는 시그니처/의사코드 수준이며 컴파일·실행·테스트는 하지 않았다.

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
  "developer_count": 2,
  "developer_weekly_hours_each": 20,
  "weeks": 4,
  "qa_count": 1,
  "qa_weekly_hours": 10,
  "coop_extra_hours": 80,
  "implementation_completed": false,
  "tests_executed": false
}
```

### Evidence

**1.** 개발 예산: 개발자 2명 × 주 20시간 × 4주 = 160시간. QA: 1명 × 주 10시간 × 4주 = 40시간.

**2.** 기본 범위 추정 합계: 이동/턴 24 + seed 맵 24 + 전투 32 + 저장/복원 24 + 화면 20 + 자동 테스트 24 + 접근성 12 = 160시간. 개발 예산 160시간과 동일하므로 core_scope_hours=160, core_fits_budget=true(여유 0).

**3.** 협동 포함: 160 + 80 = 240시간. 개발 예산 160시간을 80시간 초과하므로 coop_fits_budget=false. 병렬화해도 사람 시간 합계는 줄지 않음(자료 명시).

**4.** 디렉터리 트리(제안): src/domain/{state.ts,commands.ts,events.ts,reducer.ts,rng.ts,clock.ts,save.ts}, src/app/{turnLoop.ts,useCases.ts}, src/infra/{rngImpl.ts,clockImpl.ts,saveCodecImpl.ts}, src/render/{canvasAdapter.ts,viewModel.ts}, tests/{domain,replay,save}.

**5.** 의존 방향: domain은 외부 의존 없음(순수). app→domain. infra→domain(인터페이스 구현, domain은 infra를 모름). render→app/domain 읽기 전용, 도메인 규칙 변경 금지.

**6.** 타입/시그니처(설계 조각): type Rng = { nextInt(maxExclusive:number):number; nextFloat():number; getState():RngState; }; type Clock = { now():number; }; interface SaveCodec { encode(s:SaveData):string; decode(raw:string):SaveData; } type Command = {type:'move',dir:Dir}|{type:'attack',targetId:string}|{type:'chooseReward',rewardId:string}|{type:'endTurn'}; type Event = {type:string;turn:number;payload:unknown}; type GameState = {version:number;seed:number;turn:number;board:{w:number;h:number};player:PlayerState;rngState:RngState;status:'playing'|'won'|'lost'};

**7.** 턴 처리 pseudocode: (1) commands = collectInput(); (2) for cmd of commands: if !validate(state,cmd) continue; (3) state = reduce(state,cmd,rng,clock); // reducer 내부에서 Rng 소비 지점 고정: 이동/전투 판정 순서대로 nextInt 호출 (4) events = emit(state,cmd); (5) if turnEnded: state.turn+=1; (6) snapshot = saveCodec.encode(toSaveData(state)); 난수 소비는 reducer 내 결정적 순서로만 발생하며 render/입력 수집 단계에서는 Rng를 호출하지 않음.

**8.** 재현 테스트(설계): seed 고정 + command log replay → Event/State 해시 비교. save→load→replay 비교로 스냅샷 재현성 확인. 저장 스키마에 version 필드 포함, 버전 불일치 시 마이그레이션 또는 거부.

**9.** 렌더 경계: render는 GameState/Event를 읽기만 하고 도메인 규칙을 변경하지 않음. Canvas 어댑터는 GameState→뷰모델 순수 변환만 담당.

**10.** 미확인/미검증: 실제 컴파일·실행·테스트 미수행. 애니메이션 타이밍, 입력 큐 정책(동시 입력 순서), 저장 마이그레이션 정책은 설계 제안이며 확정 아님.

**11.** 모순/제약: 기본 범위 160시간은 개발 예산과 정확히 일치해 버퍼 0시간이므로 일정 리스크가 큼. 협동 포함 시 예산 초과로 기본 범위에서 제외해야 함.

## game-design-architecture/game_design.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-homogeneous/artifacts/game-design-architecture/game_design.json)

6×6 보드, 최대 12턴, 오프라인 1인용 제약에 맞춘 '유적의 열두 밤' 게임 기획서를 작성했다. 핵심 루프는 탐험→전투→보상→탈출의 4단계로, 각 단계에서 플레이어 선택과 결과를 정의하고 각 규칙에 재미 가설을 연결했다. 승리/패배 조건, 난이도 곡선, 전투·보상 규칙, 튜토리얼, 접근성을 구체화했으며 서버·로그인·결제·온라인 협동은 기본 범위 밖으로 명시했다. 본 문서는 설계 제안이며 실제 구현/테스트는 수행하지 않았다.

[1] 개요
- 제목: 유적의 열두 밤 (Twelve Nights of the Ruin)
- 장르: 1인용 2D 턴제 로그라이크, 브라우저 오프라인 실행
- 보드: 6×6 격자(36칸), 한 판 최대 12턴
- 목표: 12턴 안에 유적의 핵심 보물을 얻고 입구로 탈출

[2] 핵심 루프 (탐험→전투→보상→탈출)
1) 탐험: 매 턴 플레이어는 인접 4방향 중 1칸 이동 또는 '대기'를 선택. 안개가 걷히며 칸 유형(빈칸/함정/전투/보상/출구)이 드러난다.
   - 재미 가설: 36칸이라는 좁은 공간에서 정보가 점진적으로 열리므로 '어디로 갈까'라는 매 턴 의미 있는 선택이 생긴다.
2) 전투: 전투 칸 진입 시 턴을 1 소모하고 즉시 턴제 전투 시작. 플레이어는 공격/방어/도주 중 선택.
   - 재미 가설: 12턴 제약 때문에 전투 1회가 전체 자원의 1/12을 소모하므로 '싸울까 피할까'의 긴장이 발생한다.
3) 보상: 전투 승리 또는 보상 칸에서 3장 중 1장을 선택(공격력+1, 최대HP+2, 이동+1 중 하나).
   - 재미 가설: 3지선다 카드 선택은 짧은 판에서 빌드 차이를 만들어 재플레이 동기를 준다.
4) 탈출: 보물 획득 후 입구 칸으로 복귀하면 승리. 보물 없이 입구 도착 시 패배.
   - 재미 가설: '들어온 길을 되돌아 나가야 한다'는 규칙이 후반 턴을 압박해 클라이맥스를 만든다.

[3] 승리/패배
- 승리: 보물 획득 AND 입구 칸에서 턴 종료.
- 패배: HP 0, 또는 12턴 종료 시 보물 미획득/미탈출.
- 재미 가설: 두 가지 패배 경로(죽음/시간초과)가 서로 다른 압박을 주어 플레이 스타일을 분기시킨다.

[4] 난이도 곡선 (12턴 기준)
- 턴 1~3: 적 1종(HP 3, 공격 1), 함정 없음 → 학습 구간
- 턴 4~8: 적 2종 추가(HP 5, 공격 2), 함정 1~2개 → 압박 구간
- 턴 9~12: 정예 적(HP 7, 공격 3) 1마리 고정 배치, 보물방 인접 → 클라이맥스
- 재미 가설: 12턴을 3구간으로 나눠 학습→압박→클라이맥스의 리듬을 만든다.

[5] 전투 규칙
- 턴제, 플레이어 선공. 매 전투 턴마다 공격/방어/도주 선택.
- 공격: ATK만큼 적 HP 감소. 방어: 이번 턴 피해 절반(내림). 도주: 50% 성공, 실패 시 적 1회 공격.
- 전투는 최대 3라운드, 초과 시 플레이어 후퇴(턴 소모).
- 재미 가설: 3라운드 상한이 '무한 전투'를 막아 12턴 예산을 지키면서도 도주 선택의 리스크를 만든다.

[6] 보상 선택 규칙
- 보상은 3장 중 1장, 중복 없이 제시. 같은 보상 재등장 시 수치는 누적.
- 재미 가설: 선택지가 3개로 제한되어 결정 피로를 낮추고, 누적 효과가 빌드를 만든다.

[7] 튜토리얼
- 첫 판 고정 seed로 3턴 분량: 이동 1회 → 전투 1회 → 보상 1회를 강제 노출, 이후 자유 플레이.
- 재미 가설: 12턴 중 3턴만 튜토리얼로 쓰면 학습 후에도 9턴의 실제 플레이가 남아 이탈을 줄인다.

[8] 접근성
- 키보드: 방향키/WASD 이동, Space 대기, 1/2/3 선택, Esc 취소.
- 색 대비: WCAG AA(4.5:1) 이상, 색만으로 정보 전달 금지(아이콘 병기).
- 텍스트 크기: 최소 16px, 브라우저 확대 200% 지원.
- 스크린리더: 칸 유형·HP·턴 수를 aria-live로 안내.
- 재미 가설: 접근성 보강은 조작 실수로 인한 억울한 패배를 줄여 12턴 압박을 '공정한 압박'으로 만든다.

[9] 기본 범위 밖(명시적 제외)
- 서버, 로그인, 결제, 온라인 협동은 기본 범위에 포함하지 않는다. 온라인 협동은 별도 80시간 추가 시에만 검토한다.

[10] 자체 점검
- 6×6 보드 반영: O (36칸, 4방향 이동)
- 최대 12턴 반영: O (난이도 3구간, 전투 3라운드 상한)
- 각 규칙에 재미 가설 연결: O
- 제외 범위 명시: O
- 실제 구현/테스트: 미수행(설계 제안 단계)

### Facts

```json
{
  "board_width": 6,
  "board_height": 6,
  "max_turns": 12,
  "core_loop_stages": 4,
  "combat_max_rounds": 3,
  "reward_choices": 3,
  "tutorial_turns": 3,
  "difficulty_phases": 3,
  "excluded_online_coop": true,
  "excluded_server": true,
  "excluded_login": true,
  "excluded_payment": true,
  "offline_required": true,
  "implementation_done": false,
  "test_done": false
}
```

### Evidence

**1.** 보드 크기: source의 '기본 보드는 6×6' 및 task.acceptance의 '6×6 보드' 요구 → board_width=6, board_height=6, 총 36칸.

**2.** 턴 제약: source의 '한 판은 최대 12턴' → max_turns=12. 난이도 3구간(1~3, 4~8, 9~12)으로 분할.

**3.** 핵심 루프: source의 '탐험·전투·보상 선택을 거쳐 탈출하는 핵심 루프' → 4단계로 정의.

**4.** 전투 상한: 12턴 예산 보존을 위해 전투 최대 3라운드로 제한(설계 판단).

**5.** 보상: 3장 중 1장 선택은 결정 피로 최소화를 위한 설계 선택.

**6.** 튜토리얼: 12턴 중 3턴 고정 seed 노출로 학습과 실제 플레이를 분리.

**7.** 제외 범위: source의 '서버·로그인·결제·온라인 협동은 기본 범위에 없다' 및 '온라인 협동을 넣으면 별도 80시간 추가' → 기본 범위 제외 명시.

**8.** 미검증: 실제 코드 구현, 자동 테스트, 재현성 검증은 본 작업에서 수행하지 않았으므로 implementation_done=false, test_done=false로 표기.

## game-design-architecture/integration_report.json

Worker: B; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r3-game-design-architecture-homogeneous/artifacts/game-design-architecture/integration_report.json)

세 선행 산출물(budget_facts, game_design, code_architecture)을 통합해 '유적의 열두 밤' 개발 착수 자료를 작성했다. (1) 예산: 개발자 2명×20h×4주=160h, QA 1명×10h×4주=40h. 기본 범위 24+24+32+24+20+24+12=160h로 개발 예산과 정확히 일치(적합, 여유 0h). 협동 포함 160+80=240h로 80h 초과(부적합). (2) 규칙→모듈→테스트 대응표: 탐험/이동·턴→domain/reducer.ts+app/turnLoop.ts→tests/domain; seed 기반 맵→domain/rng.ts+infra/rngImpl.ts→tests/replay; 전투→domain/reducer.ts(전투 최대 3라운드)→tests/domain; 보상 3지선다→domain/commands.ts+reducer.ts→tests/domain; 저장/복원→domain/save.ts+infra/saveCodecImpl.ts→tests/save; 화면/접근성→render/canvasAdapter.ts+viewModel.ts→수동/접근성 점검(자동 테스트 범위 밖). (3) 인터페이스: GameState/Command/Event/Rng/Clock/SaveCodec 시그니처와 턴 처리 의사코드(Command 수집→검증→reducer→Event→Rng 소비 순서 고정→SaveCodec 스냅샷)를 통합. 난수는 reducer 내 결정적 순서로만 소비하고 render/입력 수집 단계에서는 Rng를 호출하지 않는다. (4) 저장 스키마에 version 필드 포함, seed+command log replay 및 save→load→replay 비교로 재현 검증. (5) 일정: 4주, 개발 160h·QA 40h, 여유 0h. 제외 범위: 서버·로그인·결제·온라인 협동(협동 시 +80h 초과). (6) 모순/미확인: 기본 범위 버퍼 0h의 일정 리스크, 협동 예산 초과, 컴파일·실행·테스트 미수행, 애니메이션 타이밍·입력 큐 정책·저장 마이그레이션 정책 미확정. 본 문서는 설계 제안이며 실제 구현/테스트는 수행하지 않았다.

[규칙→모듈→테스트 대응표]
- 탐험/이동·턴(최대 12턴): domain/reducer.ts, app/turnLoop.ts → tests/domain
- seed 기반 맵(6×6): domain/rng.ts, infra/rngImpl.ts → tests/replay
- 전투(최대 3라운드): domain/reducer.ts → tests/domain
- 보상 3지선다: domain/commands.ts, domain/reducer.ts → tests/domain
- 저장/복원(version): domain/save.ts, infra/saveCodecImpl.ts → tests/save
- 화면/접근성: render/canvasAdapter.ts, render/viewModel.ts → 수동 점검(자동 테스트 밖)

[턴 처리 의사코드]
(1) commands=collectInput(); (2) for cmd: if !validate(state,cmd) continue; (3) state=reduce(state,cmd,rng,clock) — Rng 소비 지점 고정; (4) events=emit(state,cmd); (5) if turnEnded: state.turn+=1; (6) snapshot=saveCodec.encode(toSaveData(state)).

[수용 기준]
- 같은 seed+같은 command log → 동일 Event/State 해시.
- save→load→replay 결과가 원본과 동일.
- 저장 스키마 version 불일치 시 마이그레이션 또는 거부.
- 6×6 보드, 최대 12턴, 오프라인 동작.
- render는 GameState/Event 읽기 전용, 도메인 규칙 변경 금지.

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
  "coop_over_budget_hours": 80,
  "core_budget_margin_hours": 0,
  "developer_count": 2,
  "developer_weekly_hours_each": 20,
  "qa_count": 1,
  "qa_weekly_hours": 10,
  "weeks": 4,
  "combat_max_rounds": 3,
  "reward_choices": 3,
  "tutorial_turns": 3,
  "difficulty_phases": 3,
  "core_loop_stages": 4,
  "excluded_server": true,
  "excluded_login": true,
  "excluded_payment": true,
  "excluded_online_coop": true,
  "implementation_done": false,
  "tests_executed": false,
  "save_schema_version_field": true,
  "rng_injection_required": true,
  "clock_injection_required": true,
  "render_readonly": true,
  "input_queue_policy_confirmed": false,
  "save_migration_policy_confirmed": false,
  "animation_timing_confirmed": false
}
```

### Evidence

**1.** 개발자 가용: 2명 × 20시간/주 × 4주 = 160시간. QA 가용: 1명 × 10시간/주 × 4주 = 40시간.

**2.** 기본 범위 합산: 이동/턴 24 + seed 기반 맵 24 + 전투 32 + 저장/복원 24 + 화면 20 + 자동 테스트 24 + 접근성 12 = 160시간. 개발 예산 160시간과 동일 → core_fits_budget=true, core_budget_margin_hours=0.

**3.** 협동 포함: 160 + 80 = 240시간. 240 - 160 = 80시간 초과 → coop_fits_budget=false, coop_over_budget_hours=80. 병렬화해도 사람 시간 합계 불변(자료 명시).

**4.** 고정 사실: 보드 6×6(board_width=6, board_height=6), 최대 12턴(max_turns=12), 같은 seed·같은 입력 순서 재현 필수(seeded_replay_required=true), 오프라인 동작 필수(offline_required=true).

**5.** 게임 기획: 핵심 루프 4단계(탐험→전투→보상→탈출), 난이도 3구간(1~3, 4~8, 9~12), 전투 최대 3라운드, 보상 3지선다, 튜토리얼 3턴.

**6.** 코드 구조: domain(순수 규칙)→없음, app→domain, infra→domain(인터페이스 구현), render→app/domain 읽기 전용. Rng/Clock/SaveCodec은 도메인 인터페이스로 정의하고 infra에서 주입.

**7.** 재현 설계: seed+command log replay로 Event/State 해시 비교, save→load→replay 비교, 저장 스키마에 version 필드 포함.

**8.** 미검증: 실제 컴파일·실행·자동 테스트 미수행(implementation_done=false, tests_executed=false). 애니메이션 타이밍·입력 큐 정책·저장 마이그레이션 정책은 설계 제안이며 확정 아님.

**9.** 모순/리스크: 기본 범위 160시간이 개발 예산과 정확히 일치해 버퍼 0시간으로 일정 리스크가 큼. 협동 포함 시 예산 초과로 기본 범위에서 제외해야 함.
