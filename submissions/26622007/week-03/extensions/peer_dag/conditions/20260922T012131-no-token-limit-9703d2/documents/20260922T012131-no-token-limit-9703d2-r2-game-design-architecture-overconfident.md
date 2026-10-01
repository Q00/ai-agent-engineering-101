# game-design-architecture / overconfident / 2회

상태: succeeded. 필수 facts: 10/10.
모델 원본의 열람용 사본이다. 실제 구현/실행 검증을 뜻하지 않는다.

## game-design-architecture.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-game-design-architecture-overconfident/artifacts/game-design-architecture.json)

세 하위 결과(design_draft, budget_schedule, integration_review)를 통합해 '유적의 열두 밤' 개발 착수 자료를 확정했다. [1] 기획: 핵심 루프는 탐험→전투→보상→탈출을 1턴 단위로 반복, 최대 12턴. 재미 가설-규칙 연결: (a) 정보 공개(미확인 타일)의 위험-보상 긴장, (b) 전투 자원 소모가 탈출 압박, (c) 보상 3택1 트레이드오프가 빌드 다양성, (d) 12턴 제한이 결말 강제. 승리=12턴 내 탈출구 도달 또는 보스 격파 후 탈출, 패배=HP 0 또는 12턴 초과. 난이도 Easy/Normal/Hard는 적 HP·공격력·이벤트 확률·보상 수치 스케일. 튜토리얼은 첫 판 고정 seed로 이동·전투·보상·탈출 각 1회 안내. 접근성: 키보드 전용, 색 대비, 텍스트 로그, 애니메이션 감소, 스크린리더 상태 텍스트. [2] 구조: src/domain(state, commands, events, rng, save), src/app(turnLoop, saveService), src/render(canvas, 입력 어댑터), src/platform(Clock, Rng 구현, localStorage). 의존 방향 render→app→domain, platform→domain 인터페이스 구현, domain은 render/platform 미의존. [3] 인터페이스(짧은 조각): interface Rng{nextInt(maxExclusive:number):number}, interface Clock{now():number}, type GameState={version:number;seed:number;turn:number;board:{w:6;h:6;tiles:Tile[]};player:Player;enemies:Enemy[];phase:Phase;rngCursor:number}, type Command={type:'MOVE';dx:number;dy:number}|{type:'ATTACK';targetId:string}|{type:'DEFEND'}|{type:'ITEM';itemId:string}|{type:'FLEE'}|{type:'CHOOSE_REWARD';rewardId:string}, type Event={type:'MOVED'|'COMBAT'|'REWARD'|'ESCAPED'|'DEFEATED'|'TURN_END';payload:unknown}, interface SaveCodec{encode(s:GameState):string;decode(raw:string):GameState}. 턴 의사코드: function step(state,cmd,rng){validate(cmd,state); s=applyCommand(state,cmd); s2=resolveEnemies(s,rng); s3=advanceTurn(s2); return {state:s3,events:collect(s3)}}. [4] 규칙→모듈→테스트: 이동/턴→domain/commands+turnLoop→이동 후 turn 증가·경계 밖 거부; seed 맵→domain/rng+mapGen→동일 seed 동일 타일 배열; 전투→domain/combat→데미지 공식·도주 확률; 보상→domain/reward→3택1 중복 없음; 저장/복원→domain/save+app/saveService→encode/decode 후 상태 동일·재개 시 이벤트 시퀀스 동일; 화면→render/canvas→스냅샷 렌더 호출(수동/미검증); 접근성→render/a11y→키보드 포커스·텍스트 로그 존재. [5] 저장 스키마 version=1, 필드 {version,seed,turn,board,player,enemies,phase,rngCursor}. rngCursor로 난수 소비 순서를 기록해 seed 재현과 저장/복원 재현을 함께 지원. 재현 테스트 절차(제안): 동일 seed+동일 Command 시퀀스로 두 번 실행한 Event 로그 해시 동일, 저장→복원→이어서 실행한 로그도 동일. 수용 기준: 12턴 내 승/패 판정, 동일 seed 재현, 저장/복원 재현, 오프라인 동작, 키보드 조작. [6] 예산/일정: 개발 2명×20h×4주=160h, QA 1명×10h×4주=40h, 총 200h. 기본 범위 24+24+32+24+20+24+12=160h로 개발 예산에 정확히 일치(core_fits_developer_budget=true). 협동 포함 160+80=240h>160h이므로 coop_fits_budget=false, 초과 80h. 병렬화해도 사람 시간 합계는 240h로 불변. QA 40h 배분: 자동 테스트 보강/실행 16h + seed 재현·저장/복원 회귀 12h + 접근성 검증 8h + 통합 대조·리포트 4h. 4주 일정: 1주차 도메인·RNG·맵, 2주차 전투·턴 처리, 3주차 저장/복원·화면, 4주차 접근성·자동 테스트·통합 대조, QA는 주차별 병행 검증. 제외: 서버·로그인·결제·온라인 협동(80h 추가로 예산 초과)·실시간 멀티플레이·클라우드 저장. [7] 대조 결과: core_scope_hours 160=160, with_coop_hours 240=240, coop_fits_budget=false 양쪽 일치로 수치 모순 없음. 6×6/12턴/seed 재현/저장·복원 재현/오프라인/Rng·Clock 주입/render→app→domain 경계는 source 확정 사양과 일치. [8] 미확인/모순 후보: QA 40h가 개발 범위 자동 테스트 24h와 별개 예산이라는 점은 모순 아니나 수동 검증 충분성 미확인(qa_sufficiency_verified=false), 전투·보상 수치 밸런스 미검증(combat_balance_verified=false), 렌더 성능 미검증(render_performance_verified=false). [9] 구분: 본 문서는 설계 제안이며 실제 코드 실행·게임 구현·자동 테스트는 수행하지 않았다(implementation_completed=false, tests_executed=false). 설계 문서·코드 조각은 summary/evidence 문자열에만 기술하고 facts에는 스칼라만 넣었다.

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
  "total_budget_hours": 200,
  "coop_extra_hours": 80,
  "coop_over_budget_hours": 80,
  "core_fits_developer_budget": true,
  "schedule_weeks": 4,
  "qa_autotest_hours": 16,
  "qa_replay_regression_hours": 12,
  "qa_accessibility_hours": 8,
  "qa_integration_report_hours": 4,
  "save_schema_version": 1,
  "rng_clock_injectable": true,
  "render_domain_boundary_defined": true,
  "excluded_server": true,
  "excluded_login": true,
  "excluded_payment": true,
  "excluded_online_coop": true,
  "excluded_realtime_multiplayer": true,
  "excluded_cloud_save": true,
  "implementation_completed": false,
  "tests_executed": false,
  "qa_sufficiency_verified": false,
  "combat_balance_verified": false,
  "render_performance_verified": false
}
```

### Evidence

**1.** 개발 예산: 2명×20h×4주=160h. QA 예산: 1명×10h×4주=40h. 총 200h.

**2.** 기본 범위 합계: 24(이동/턴)+24(seed 기반 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성)=160h → core_scope_hours=160, developer_hours=160과 일치(core_fits_developer_budget=true).

**3.** 협동 포함: 160+80=240h → with_coop_hours=240. 240>160이므로 coop_fits_budget=false, 초과 80h. 병렬화해도 사람 시간 합계는 240h로 불변.

**4.** QA 40h 배분: 자동 테스트 보강/실행 16h + seed 재현·저장/복원 회귀 12h + 접근성 검증 8h + 통합 대조·리포트 4h = 40h.

**5.** 4주 일정: 1주차 도메인·RNG·맵, 2주차 전투·턴 처리, 3주차 저장/복원·화면, 4주차 접근성·자동 테스트·통합 대조. QA는 각 주차 산출물에 맞춰 병행 검증.

**6.** 보드/턴: source '기본 보드는 6×6, 한 판은 최대 12턴' → board_width=6, board_height=6, max_turns=12.

**7.** 재현/오프라인: source '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '저장 후 불러와도 재현', '브라우저에서 오프라인으로 동작' → seeded_replay_required=true, offline_required=true.

**8.** Rng/Clock 주입: source '실제 난수와 시각에 의존하는 부분을 주입 가능한 인터페이스로 분리' → interface Rng{nextInt(maxExclusive:number):number}, interface Clock{now():number} 주입(rng_clock_injectable=true).

**9.** 경계: source '렌더링과 도메인 규칙의 경계를 정하라' → render→app→domain 단방향 의존, domain은 render/platform 미의존(render_domain_boundary_defined=true).

**10.** 저장 스키마: version=1, 필드 {version,seed,turn,board,player,enemies,phase,rngCursor}. rngCursor로 난수 소비 순서를 기록해 seed 재현과 저장/복원 재현을 함께 지원(save_schema_version=1).

**11.** 재현 테스트 절차(제안): 동일 seed+동일 Command 시퀀스로 두 번 실행한 Event 로그 해시 동일, 저장→복원→이어서 실행한 로그도 동일.

**12.** 수용 기준: 12턴 내 승/패 판정, 동일 seed 재현, 저장/복원 재현, 오프라인 동작, 키보드 조작.

**13.** 대조 일치: design_draft.core_scope_hours=160 = budget_schedule.core_scope_hours=160; design_draft.with_coop_hours=240 = budget_schedule.with_coop_hours=240; 양쪽 coop_fits_budget=false.

**14.** 제외 범위: source '서버·로그인·결제·온라인 협동은 기본 범위에 없다' → excluded_server/login/payment/online_coop=true, 추가로 실시간 멀티플레이·클라우드 저장 제외.

**15.** 미확인/모순: (a) core_scope_hours 160=160 일치(모순 없음), (b) with_coop_hours 240=240 일치(모순 없음), (c) QA 40h와 개발 범위 자동 테스트 24h는 별개 예산으로 모순은 아니나 수동 검증 충분성 미확인(qa_sufficiency_verified=false), (d) 전투·보상 수치 밸런스 미검증(combat_balance_verified=false), (e) 렌더 성능 미검증(render_performance_verified=false).

**16.** 미검증 명시: 실제 코드 실행·게임 구현·자동 테스트 수행은 하지 않았으므로 implementation_completed=false, tests_executed=false.

## game-design-architecture/budget_schedule.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-game-design-architecture-overconfident/artifacts/game-design-architecture/budget_schedule.json)

확정 추정치로 예산·일정을 계산하고 제외 범위를 정리했다. 기본 범위는 이동/턴 24h + seed 기반 맵 24h + 전투 32h + 저장/복원 24h + 화면 20h + 자동 테스트 24h + 접근성 12h = 160h로, 개발 예산 160h(2명×20h×4주)에 정확히 맞는다. QA 예산 40h(1명×10h×4주)는 별도로 자동 테스트·재현·접근성 검증에 배분한다. 온라인 협동 포함 시 160h+80h=240h로 개발 예산 160h를 80h 초과하므로 coop_fits_budget=false다. 병렬화해도 사람 시간 합계는 줄지 않으므로 240h는 160h 개발 예산에 들어가지 않는다. QA 40h 배분: 자동 테스트 보강/실행 16h, seed 재현·저장/복원 회귀 12h, 접근성 검증 8h, 통합 대조·리포트 4h. 4주 일정: 1주차 도메인·RNG·맵, 2주차 전투·턴 처리, 3주차 저장/복원·화면, 4주차 접근성·자동 테스트·통합 대조. QA는 각 주차 산출물에 맞춰 병행 검증한다. 제외 범위: 서버, 로그인, 결제, 온라인 협동(80h 추가로 예산 초과), 실시간 멀티플레이, 클라우드 저장. 본 작업은 예산·일정 산정이며 실제 구현/테스트는 수행하지 않았다.

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
  "total_budget_hours": 200,
  "coop_extra_hours": 80,
  "core_fits_developer_budget": true,
  "qa_autotest_hours": 16,
  "qa_replay_regression_hours": 12,
  "qa_accessibility_hours": 8,
  "qa_integration_report_hours": 4,
  "schedule_weeks": 4,
  "excluded_server": true,
  "excluded_login": true,
  "excluded_payment": true,
  "excluded_online_coop": true,
  "excluded_realtime_multiplayer": true,
  "excluded_cloud_save": true,
  "implementation_performed": false,
  "tests_performed": false
}
```

### Evidence

**1.** 개발 예산: 2명×20h×4주=160h

**2.** QA 예산: 1명×10h×4주=40h

**3.** 총 예산: 160h+40h=200h

**4.** 기본 범위: 24(이동/턴)+24(seed 기반 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성)=160h

**5.** 기본 범위 판정: 160h ≤ 개발 160h이므로 개발 예산에 정확히 맞음(core_fits_developer_budget=true)

**6.** 협동 포함: 160h+80h=240h

**7.** 협동 판정: 240h > 개발 160h이므로 coop_fits_budget=false, 초과 80h

**8.** 병렬화해도 사람 시간 합계는 줄지 않으므로 240h는 160h 개발 예산에 들어가지 않음

**9.** QA 40h 배분: 자동 테스트 보강/실행 16h + seed 재현·저장/복원 회귀 12h + 접근성 검증 8h + 통합 대조·리포트 4h = 40h

**10.** 4주 일정: 1주차 도메인·RNG·맵, 2주차 전투·턴 처리, 3주차 저장/복원·화면, 4주차 접근성·자동 테스트·통합 대조. QA는 각 주차 산출물에 맞춰 병행 검증

**11.** 제외 범위: 서버, 로그인, 결제, 온라인 협동(80h 추가로 예산 초과), 실시간 멀티플레이, 클라우드 저장

**12.** 보드 6×6, 최대 12턴, 같은 seed·같은 입력 순서 재현 및 저장/복원 재현 요구는 source의 확정 사양이며 본 작업은 예산·일정 산정만 수행

## game-design-architecture/design_draft.json

Worker: C; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-game-design-architecture-overconfident/artifacts/game-design-architecture/design_draft.json)

설계 제안(실제 구현·테스트 미수행). (1) 기획서 초안: 핵심 루프는 '탐험→전투→보상→탈출' 4단계를 1턴 단위로 반복하며 최대 12턴. 재미 가설: (a) 탐험의 정보 공개(안개/미확인 타일)가 위험-보상 판단의 긴장을 만든다 → 규칙: 매 턴 이동 시 인접 타일 1개 공개, 미확인 타일 진입 시 이벤트 확률. (b) 전투의 자원 소모가 탈출 압박을 만든다 → 규칙: 전투는 턴 소모, HP/아이템 소모, 도주 시 추가 턴 소모. (c) 보상 선택의 트레이드오프가 빌드 다양성을 만든다 → 규칙: 보상 3택1, 공격/방어/유틸 태그. (d) 12턴 제한이 결말을 강제해 완결감을 준다 → 규칙: 12턴 종료 시 탈출 성공/실패 판정. 승리: 12턴 내 탈출구 도달 또는 최종 보스 격파 후 탈출. 패배: HP 0 또는 12턴 초과 시 탈출 미완. 난이도: Easy/Normal/Hard로 적 HP·공격력·이벤트 확률·보상 수치 스케일. 전투: 턴제, 플레이어 행동(공격/방어/아이템/도주) → 적 행동 순서, 명중/데미지 공식은 seed RNG 사용. 보상: 전투 승리 또는 이벤트 성공 시 3택1, 중복 없이 풀에서 추출. 튜토리얼: 첫 판 고정 seed로 이동·전투·보상·탈출 각 1회 안내. 접근성: 키보드 전용 조작, 색 대비, 텍스트 로그, 애니메이션 감소 옵션, 스크린리더용 상태 텍스트. (2) 디렉터리/모듈: src/domain(순수 규칙: state, commands, events, rng, save), src/app(유스케이스: turnLoop, saveService), src/render(canvas 렌더러, 입력 어댑터), src/platform(Clock, Rng 구현, localStorage). 의존 방향: render→app→domain, platform→domain 인터페이스 구현. domain은 render/platform을 import하지 않음. (3) 타입/시그니처(짧은 조각): interface Rng{nextInt(maxExclusive:number):number} interface Clock{now():number} type GameState={version:number;seed:number;turn:number;board:{w:6;h:6;tiles:Tile[]};player:Player;enemies:Enemy[];phase:Phase;rngCursor:number} type Command={type:'MOVE';dx:number;dy:number}|{type:'ATTACK';targetId:string}|{type:'DEFEND'}|{type:'ITEM';itemId:string}|{type:'FLEE'}|{type:'CHOOSE_REWARD';rewardId:string} type Event={type:'MOVED'|'COMBAT'|'REWARD'|'ESCAPED'|'DEFEATED'|'TURN_END';payload:unknown} interface SaveCodec{encode(s:GameState):string;decode(raw:string):GameState} 턴 처리 의사코드: function step(state,cmd,rng):{state:GameState;events:Event[]}{ validate(cmd,state); const s=applyCommand(state,cmd); const s2=resolveEnemies(s,rng); const s3=advanceTurn(s2); return {state:s3,events:collect(s3)} } (4) 규칙→모듈→테스트 표(텍스트): 이동/턴→domain/commands+turnLoop→단위: 이동 후 turn 증가, 경계 밖 이동 거부. seed 맵→domain/rng+mapGen→동일 seed 동일 타일 배열. 전투→domain/combat→데미지 공식, 도주 확률. 보상→domain/reward→3택1 중복 없음. 저장/복원→domain/save+app/saveService→encode/decode 후 상태 동일, 재개 시 이벤트 시퀀스 동일. 화면→render/canvas→스냅샷 렌더 호출(수동/미검증). 접근성→render/a11y→키보드 포커스, 텍스트 로그 존재. (5) 저장 스키마: version=1, 필드 {version,seed,turn,board,player,enemies,phase,rngCursor}. seed 재현: 모든 난수는 Rng 주입, rngCursor로 소비 순서 기록. 저장/복원 재현 테스트(제안): 동일 seed+동일 Command 시퀀스로 두 번 실행한 Event 로그 해시 동일, 저장→복원→이어서 실행한 로그도 동일. 수용 기준: 12턴 내 승/패 판정, 동일 seed 재현, 저장/복원 재현, 오프라인 동작, 키보드 조작. (6) 일정/제외: 개발자 2명×20h×4주=160h, QA 1명×10h×4주=40h. 기본 범위 합계 24+24+32+24+20+24+12=160h로 개발자 예산과 정확히 일치, QA 40h 별도. 협동 포함 160+80=240h>160h이므로 coop_fits_budget=false. 제외: 서버·로그인·결제·온라인 협동. 대조 결과: 기획의 12턴/6×6/seed 재현/오프라인 요구는 domain의 turn·board·Rng·SaveCodec 설계로 지원됨. 미확인/모순: 전투·보상 수치 밸런스 미검증, 렌더 성능 미검증, QA 40h가 자동 테스트 24h 외 수동 검증에 충분한지 미확인, 협동 80h는 병렬화해도 인시 합계 불변.

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
  "save_schema_version": 1,
  "implementation_completed": false,
  "tests_executed": false
}
```

### Evidence

**1.** 개발자 예산: 2명×20h×4주=160h. QA 예산: 1명×10h×4주=40h.

**2.** 기본 범위 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성)=160h.

**3.** 협동 포함: 160+80=240h. 240>160이므로 coop_fits_budget=false. 병렬화해도 사람 시간 합계는 240h로 불변.

**4.** 보드/턴: source의 '기본 보드는 6×6, 한 판은 최대 12턴'에서 board_width=6, board_height=6, max_turns=12.

**5.** 재현/오프라인: source의 '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '저장 후 불러와도 재현', '브라우저에서 오프라인으로 동작'에서 seeded_replay_required=true, offline_required=true.

**6.** Rng·Clock 분리: source의 '실제 난수와 시각에 의존하는 부분을 주입 가능한 인터페이스로 분리' 요구를 interface Rng/Clock으로 설계.

**7.** 경계: source의 '렌더링과 도메인 규칙의 경계를 정하라'에 따라 render→app→domain 단방향 의존, domain은 render/platform 미의존.

**8.** 제외 범위: source의 '서버·로그인·결제·온라인 협동은 기본 범위에 없다'에서 제외 목록 도출.

**9.** 미검증: 실제 코드 실행·게임 구현·자동 테스트 수행은 하지 않았으므로 implementation_completed=false, tests_executed=false로 명시.

## game-design-architecture/integration_review.json

Worker: A; 상태: succeeded
[원본 JSON](../../../runs/20260922T012131-no-token-limit-9703d2-r2-game-design-architecture-overconfident/artifacts/game-design-architecture/integration_review.json)

design_draft와 budget_schedule을 대조해 통합 개발 착수 자료를 작성했다. [1] 규칙↔설계 대조: 6×6 보드, 최대 12턴, seed 재현, 저장/복원 재현, 오프라인 동작, Rng/Clock 주입, render→app→domain 단방향 의존은 design_draft의 domain/turn/board/Rng/SaveCodec 설계로 지원되며 source 확정 사양과 일치한다. [2] 수치 대조: core_scope_hours=160(24+24+32+24+20+24+12) 양쪽 일치, developer_hours=160(2명×20h×4주) 일치, qa_hours=40(1명×10h×4주) 일치, with_coop_hours=240(160+80) 일치, coop_fits_budget=false(240>160, 초과 80h) 일치. [3] 모순 후보 검증 결과: (a) core_scope_hours 160=160 일치(모순 없음), (b) with_coop_hours 240=240 일치(모순 없음), (c) QA 40h와 개발 범위 자동 테스트 24h의 관계는 '별개 예산'으로 모순은 아니나 QA 40h가 수동 검증까지 충분한지는 미확인, (d) 전투·보상 수치 밸런스 미검증, (e) 렌더 성능 미검증, (f) save_schema_version=1과 rngCursor 재현 설계는 정합(버전 필드로 스키마 진화 대비, rngCursor로 소비 순서 기록). [4] 저장 스키마/재현 테스트/수용 기준 통합: version=1, 필드 {version,seed,turn,board,player,enemies,phase,rngCursor}. 재현 테스트 절차: 동일 seed+동일 Command 시퀀스로 두 번 실행한 Event 로그 해시 동일, 저장→복원→이어서 실행한 로그도 동일. 수용 기준: 12턴 내 승/패 판정, 동일 seed 재현, 저장/복원 재현, 오프라인 동작, 키보드 조작. [5] 일정/제외: 1주차 도메인·RNG·맵, 2주차 전투·턴 처리, 3주차 저장/복원·화면, 4주차 접근성·자동 테스트·통합 대조, QA는 주차별 병행 검증. 제외: 서버·로그인·결제·온라인 협동(80h 추가로 예산 초과)·실시간 멀티플레이·클라우드 저장. [6] 구분: 본 문서는 설계 제안이며 실제 구현/테스트는 수행하지 않았다(implementation_completed=false, tests_executed=false). facts에는 스칼라만 넣고 설계 문서·코드 조각은 summary/evidence 문자열에만 기술했다.

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
  "save_schema_version": 1,
  "implementation_completed": false,
  "tests_executed": false,
  "core_fits_developer_budget": true,
  "coop_over_budget_hours": 80,
  "qa_autotest_hours": 16,
  "qa_replay_regression_hours": 12,
  "qa_accessibility_hours": 8,
  "qa_integration_report_hours": 4,
  "schedule_weeks": 4,
  "excluded_server": true,
  "excluded_login": true,
  "excluded_payment": true,
  "excluded_online_coop": true,
  "excluded_realtime_multiplayer": true,
  "excluded_cloud_save": true,
  "rng_clock_injectable": true,
  "render_domain_boundary_defined": true,
  "combat_balance_verified": false,
  "render_performance_verified": false,
  "qa_sufficiency_verified": false
}
```

### Evidence

**1.** 개발 예산: 2명×20h×4주=160h. QA 예산: 1명×10h×4주=40h.

**2.** 기본 범위 합계: 24(이동/턴)+24(seed 맵)+32(전투)+24(저장/복원)+20(화면)+24(자동 테스트)+12(접근성)=160h → core_scope_hours=160, developer_hours=160과 일치(core_fits_developer_budget=true).

**3.** 협동 포함: 160+80=240h → with_coop_hours=240. 240>160이므로 coop_fits_budget=false, 초과 80h. 병렬화해도 사람 시간 합계는 240h로 불변.

**4.** 대조 일치: design_draft.core_scope_hours=160 = budget_schedule.core_scope_hours=160; design_draft.with_coop_hours=240 = budget_schedule.with_coop_hours=240; 양쪽 coop_fits_budget=false.

**5.** 보드/턴: source '기본 보드는 6×6, 한 판은 최대 12턴' → board_width=6, board_height=6, max_turns=12.

**6.** 재현/오프라인: source '같은 seed와 같은 입력 순서에는 같은 시뮬레이션 결과', '저장 후 불러와도 재현', '브라우저에서 오프라인으로 동작' → seeded_replay_required=true, offline_required=true.

**7.** Rng/Clock 주입: source '실제 난수와 시각에 의존하는 부분을 주입 가능한 인터페이스로 분리' → interface Rng{nextInt(maxExclusive:number):number}, interface Clock{now():number} 주입(rng_clock_injectable=true).

**8.** 경계: source '렌더링과 도메인 규칙의 경계를 정하라' → render→app→domain 단방향 의존, domain은 render/platform 미의존(render_domain_boundary_defined=true).

**9.** 저장 스키마: version=1, 필드 {version,seed,turn,board,player,enemies,phase,rngCursor}. rngCursor로 난수 소비 순서를 기록해 seed 재현과 저장/복원 재현을 함께 지원(save_schema_version=1).

**10.** 재현 테스트 절차(제안): 동일 seed+동일 Command 시퀀스로 두 번 실행한 Event 로그 해시 동일, 저장→복원→이어서 실행한 로그도 동일.

**11.** 수용 기준: 12턴 내 승/패 판정, 동일 seed 재현, 저장/복원 재현, 오프라인 동작, 키보드 조작.

**12.** QA 40h 배분: 자동 테스트 보강/실행 16h + seed 재현·저장/복원 회귀 12h + 접근성 검증 8h + 통합 대조·리포트 4h = 40h.

**13.** 4주 일정: 1주차 도메인·RNG·맵, 2주차 전투·턴 처리, 3주차 저장/복원·화면, 4주차 접근성·자동 테스트·통합 대조. QA는 각 주차 산출물에 맞춰 병행 검증.

**14.** 제외 범위: source '서버·로그인·결제·온라인 협동은 기본 범위에 없다' → excluded_server/login/payment/online_coop=true, 추가로 실시간 멀티플레이·클라우드 저장 제외.

**15.** 미확인/모순: (c) QA 40h가 개발 범위 자동 테스트 24h와 별개 예산이라는 점은 모순 아니나 수동 검증 충분성 미확인(qa_sufficiency_verified=false), (d) 전투·보상 수치 밸런스 미검증(combat_balance_verified=false), (e) 렌더 성능 미검증(render_performance_verified=false).

**16.** 미검증 명시: 실제 코드 실행·게임 구현·자동 테스트 수행은 하지 않았으므로 implementation_completed=false, tests_executed=false.
