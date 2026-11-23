# 아키텍처 그림 대응표 (architecture.tex의 상자와 화살표 → 코드 위치)

- 기준: 경로는 submissions/25520013/week-08 기준. 운영 층은 r3-attempt2/의 코드, 규칙 훅(r3/hooks/, r3/policy.py, r3/aggregates.py)과 키트(kit/market_kit.py)는 배포한 컴퓨터(labpc)의 실행본 기준. 과목 키트 원본은 upstream/main 기준. [배포]를 붙인 내용은 labpc의 실행 상태로 확인한 것임
- 그림 파일: architecture.tex, architecture.pdf(폴더 맨 위)
- 상자 테두리: 굵은 실선은 키트 기본형에 없는 부분, 가는 실선은 키트와 시장 그대로, 겹선은 사람이 쓰는 시장 설명서, 점선과 흰 채움은 a2/LIVE가 있으면 새 거래를 분기하는 모델 경로([배포] 마감 직전 실행 기록에서 모델 거래 이벤트 미관측). 이 구분은 상자에만 적용하고 화살표는 모두 같은 굵기임
- 빌드: XeLaTeX(kotex, TikZ)와 글꼴 Apple SD Gothic Neo가 필요함
- 색은 층을 나타냄(시장과 키트 회색, 거래 경로 주황, 운영 LLM 파랑, 운영 기반 청록, 메모리 보라, 메타하네스 빨강, 사람 갈색)
- 상자 문구는 짧게 두고, 조건과 예외, 근거 줄은 아래 표의 "그림에서 뺀 조건" 칸에 적음

## 상자

| 상자 | 테두리 | 코드 위치 | 커밋 | 그림에서 뺀 조건, 확인하지 못한 점 |
| --- | --- | --- | --- | --- |
| 시장 | 가는 실선 | 일정 근거: upstream/main 3dd6116 week-08.html:288 (Round 3 11-03 시작, 11-17 23:59 종료). 키트 근거: upstream scripts/market_kit.py:1-20 | 일정 3dd6116, 키트 4bd1a6a (upstream, 키트 v4) | 4bd1a6a 시점의 week-08.html은 11/02~11/16 일정이라 일정 근거로 쓰지 않음 |
| 키트 v4 ("과목 제공, 차단 구매자 거절만 더함") | 가는 실선 | upstream scripts/market_kit.py:1-39 (agent.json 훅 계약), 80 KIT_VERSION, 314 AcpShop, 887 restock; 배포본 kit/market_kit.py:315-376, 425-436 (차단 구매자 거절), r3/tools/block_poller.py | 4bd1a6a (과목 키트), 24cb2f6 (배포본 복사) | [배포] 과목 키트와 다른 곳은 차단 구매자 거절 부분과 import math 한 줄뿐임. 확인 중 오류가 나면 판매함 |
| 규칙 훅 (r3/), 상자 문구 "정책 값을 읽고 거래를 결정" | 굵은 실선 | 훅 연결 r3/agent.json:2-6. 정책 로딩과 결정: r3/hooks/seller.py:577-584, 610-626; r3/hooks/market_buyer.py:676-685, 726; r3/hooks/restock.py:213-218 | 24cb2f6 (배포본 복사) | 판매 훅은 구매자가 낸 가격을 규칙으로 읽지 못하면 설정에 따라 r3/llm/live_v120c.py에 해석을 요청함(r3/hooks/seller.py:621-624, 682-686, 설정 r3/llm_config.json). [배포] 배포한 설정에서 이 기능은 켜져 있다(r3/llm_config.json과 r3/llm_caps_config_v4.json이 배포 호스트의 같은 이름 파일과 바이트가 같다) |
| 정책 파일 (policy.json: 정책 값 / policy_arms.json: 시험군에 적용할 정책 값, 두 파일) | 굵은 실선 | 쓰는 길: r3-attempt2/kernel/operator.py:1427-1450 _apply_policy → r3-attempt2/kernel/experiments.py:742-748 → r3-attempt2/kernel/policy_edit.py:549 apply_many, 376-385 _swap. 시험군 저장: experiments.py:90 ARMS_FILE, 561-570. 판매 훅은 policy_arms.json에서 시험군에 적용할 정책 값을 읽음: r3/hooks/seller.py:530-539 deal_policy | 1609dea (동시 비교 시험군), 5dea548 (쓰기 잠금) | 동시 비교 시험군은 policy.json 안이 아니라 별도 파일 policy_arms.json에 있음. 두 파일은 운영자가 실행 중에 고치므로, 제출한 r3/policy.json은 초기값이고 마감 때의 값은 아님 |
| 모델 거래 경로 (훅에 연결; 상자 문구 "모델 거래 경로 (훅에 연결)", "새 거래 분기 조건: a2/LIVE", "마감 직전 모델 거래 이벤트 미관측") | 점선 | r3-attempt2/kernel/seller_bridge.py:1-20, 29 FLAG; r3-attempt2/kernel/agent.py:1-12; r3-attempt2/kernel/buyer_exec.py:1-12; r3-attempt2/live/hook_patch.py:20-33; r3-attempt2/live/seller_patch.py; 배포한 훅의 분기 r3/hooks/seller.py:37-41, 570-571, r3/hooks/market_buyer.py:936-939, r3/hooks/restock.py:256-267 | 없음 (이번 확인에서 특정 안 함) | 스위치 a2/LIVE는 새 판매만 분기하고 이미 열린 판매는 그 경로로 이어짐 (seller_bridge.py:241-243 wants, 332-354 _open). 구매와 재입고 패치도 같은 스위치를 씀 (hook_patch.py:20-33, buyer_exec.py:353-363). [배포] 마감 직전 구간(11-17 22:08~23:59)의 실행 기록에 모델 거래 결정 행과 구매 실행 행이 없다 |
| 운영 LLM 영역 ("gpt-6.1-terra, 기본 추론 강도 medium") | 굵은 실선 | r3-attempt2/kernel/llm.py:21 MODEL, 101-120 build_cmd; r3-attempt2/live/a2ctl.sh:36 effort medium | 97716c7 | [배포] 마감 전 마지막 구간(11-17 22:08~23:59)의 모델 호출은 모두 gpt-6.1-terra이고, 운영자 medium, 기억 서버 low, 감사자 medium임 |
| 가게 운영자, 구매 운영자 (기본 20분마다 점검, 예약 시각이나 사건에 맞춰 점검) | 굵은 실선 | r3-attempt2/kernel/operator.py:1-14, 100 ROLE_INTERVAL_S, 102 SHOP_LEAD_S, 1020-1066 _wake_reason_compact, 291 role_schema; a2ctl.sh:33-36 | edaa3d4 (역할 분리) | 가게 운영자는 재입고 가능 시간대 시작 5분 전에도 점검함(102 SHOP_LEAD_S 300). 이것은 운영자가 다음 점검을 예약하지 않았고 재입고 가능 시간대 시작의 유효한 추정이 있을 때의 기본값임(operator.py:931-942 _window_reason, 1040-1053). 사건은 실험 판정과 순위 급변(1020-1066). [배포] 마감 전 마지막 구간에 가게 13번, 구매 23번 점검했고, 대부분 운영자의 다음 점검 예약으로 시작함 |
| 조사원 ("질문 하나를 여러 단계로 조사해 결과를 기록") | 굵은 실선 | r3-attempt2/kernel/analyst.py:1-15, 100 Analyst; r3-attempt2/kernel/questions.py:121 ask, 200 add_finding; a2ctl.sh:40-41 | 0d31a9d | a2ctl.sh 기본 시작 목록(27줄)에 analyst가 없으므로, 조사원은 실행 스크립트에서 이름을 지정해 시작함. [배포] 실행 기록에는 마감까지 조사원 실행이 280회 남아 있고, 마감 직전 구간에는 조사 질문이 없어 조사원의 모델 호출이 없었다 |
| 위임 에이전트 | 굵은 실선 | r3-attempt2/kernel/agent_registry.py:1-13; r3-attempt2/kernel/agents.py:1-15, 78 CreatedAgent, 253 poll | cf273b6 | 배포 호스트에서 위임 에이전트 실행기가 시작됐는지와 마감 때 등록된 에이전트 수는 확인하지 않았다 |
| 컨텍스트 조립 ("점검마다 새 컨텍스트", "도구 결과가 길어지면 요약") | 굵은 실선 | r3-attempt2/kernel/operator.py:1067-1126 _wake_compact, 111-114 관찰 예산, 123 COMPACT_AT, 128 COMPACT_MIN_OLD, 129 COMPACT_RECENT, 138 COMPACT_FAILS, 1253-1300 _compact; r3-attempt2/kernel/brief.py:2156 COMPACT_CHARS, 2517-2557 build_compact | 7222cd6, fd4731a, 6feb3c2 | 요약 조건: 기존 요약과 남긴 결론, 관찰을 합친 길이가 60,000자(123) 이상이고 최근 2단계를 뺀 이전 부분이 30,000자(128) 이상이며 요약 실패가 2번(138) 미만일 때. 최근 2단계는 원문 유지. 길이 계산에는 남긴 결론도 들어가고, 기존 요약은 전체 길이에만 더하고 이전 부분에는 넣지 않음(operator.py:1260-1271). 실제 프롬프트 크기는 확인하지 않음 |
| 공용 도구함 ("기본 도구 24개 (숨김 전, recall 포함)") | 굵은 실선 | r3-attempt2/kernel/tools.py:909 RECOMMENDED, 917-945 TOOLS (27개), 794 tool_search, 459 recall, 760 analyze; operator.py:699-716 listed (하네스 도구 3개 제외해 24개); r3-attempt2/kernel/code_tools.py:109 check, 198 execute; r3-attempt2/kernel/made_tools.py:488 add, 533 remove (감사자의 만든 q_ 도구 삭제는 auditor.py:1140-1156) | 92a9ede, b238d68 | [배포] 마감 전 실행 기록에 조사원이 등록한 q_ 도구 14개, 이 가운데 13개를 합계 365회 호출, 마감 전 삭제 기록 없음 |
| 변경 적용 커널 ("code_edit: 시험, 30분 감시, 이상 시 복구") | 굵은 실선 | operator.py:228-248 COMPACT_SCHEMA, 1394 _finish_compact_locked, 1427 _apply_policy; experiments.py:746-748 → policy_edit.py:549 apply_many; r3-attempt2/kernel/code_edit.py:1-14, 137 WATCH_S 1800, 1477 revert, 1814 watch, 592 protected_error, 634 danger; experiments.py:1-20, 658 apply_policy, 1310 evaluate, 1424 evaluate_split, 1771 tick | a87b613 (판정은 재고 알리기만) | 실험 판정 수와 코드 수정 수는 확인하지 않음 |
| 검색용 기억 memd | 굵은 실선 | r3-attempt2/memd/server.py:1-14, 34 EMBED_MODEL, 56 build_memory, 107 MemService; r3-attempt2/kernel/memfeed.py:1-20, 273 run_once; a2ctl.sh:31 (300초); r3-attempt2/kernel/memcheck.py:1-20, 205 run | 6acc3cb (점검 요약을 기억으로) | [배포] 기억 저장 3,428번, 추출 사실 13,059개, 검색 20,430번. memcheck는 운영자가 done에 요청할 때만 실행되며(operator.py:1763-1764) 마감까지 실행 기록이 없음. qdrant에 남은 기억 수는 확인하지 않음 |
| 실행 기록 ("추가만"은 trajectory.jsonl과 findings.jsonl에만) | 굵은 실선 | r3-attempt2/kernel/trajectory.py:1-19; experiments.py:44 LEDGER; questions.py:1-15 (findings.jsonl); operator.py:1302-1313 _hand_over | 없음 | experiments.json은 실험 상태를 고쳐 다시 쓰는 파일이라 "추가만"에서 뺌. [배포] W/a2/trajectory.jsonl은 127,868행(46 MB)이며 제출하지 않음 |
| 과정 감사 | 굵은 실선 | r3-attempt2/kernel/audit.py:1-13 | 2b0e34d (11-11 08:10, 파일을 처음 넣은 커밋) | 없음 |
| 감사자 ("기본 1시간 또는 판정 3건", "실수 증가 기준을 넘으면 지침 복구") | 굵은 실선 | r3-attempt2/kernel/auditor.py:1-13, 60 INTERVAL_S, 61 MIN_GAP_S, 62 VERDICTS_TRIGGER, 63 MIN_WAKES, 381-383 _interval_s, 400-410 due, 421-449 judge_guidance, 427 judge_guidance, 1029 tells_value, 1034 _apply_guidance, 1076 _apply_tools, 872 _lifecycle_block, 1234 run, 1344 maybe_run; 운영자 프로세스에서 부름 operator.py:1835 | 4559462 (1시간), 38e2c28 (도구 수명) | 판정 3건 경로에도 마지막 실행 뒤 최소 30분 간격(61)이 있음. 지침 복구는 변경 전후 점검이 각각 4번(auditor.py:63 MIN_WAKES) 이상 쌓인 뒤, 점검당 실수 수가 변경 전 값의 1.25배에 0.5를 더한 값보다 크면 함(64-67, 421-449). [배포] 마감까지 감사자 기록 85건(감사 완료 84, 건너뜀 1), 지침 변경 11건, 되돌림 1건 |
| 사람 (MARKET_BRIEF.md, "목표, 안전선, 확인한 내용을 기록") | 겹선 | 파일: r3-attempt2/MARKET_BRIEF.md (tools.py:477 GUIDE_PATH). 역할 운영자에게 들어가는 길: operator.py:1105 tools.guide_always() → tools.py:526-536 (설명서 목차, 1절, 7절 전문; 나머지 절은 guide 도구로 읽음), tools.py:477-485 _guide_sections | eb14107 | 없음 |

## 화살표

화살표는 모두 같은 굵기로 그림. 아래 표의 굵기 칸은 없앰

| 화살표 | 코드 위치 |
| --- | --- |
| 시장 ↔ 키트 (요청, 주문, 공급, 정산) | upstream scripts/market_kit.py:1-20 |
| 키트 → 규칙 훅 (훅 호출) | r3/agent.json:3-6, upstream market_kit.py:887-899 restock |
| 정책 파일 → 규칙 훅 ("훅에서 정책 적용", 규칙 훅 상자 동쪽 직선 테두리에 닿음) | 읽는 때: 판매는 새 주문 때 확인함(r3/hooks/seller.py:577-584, refresh_policy 343; 열린 거래는 연 때의 정책 유지). 파일 재확인의 기본 간격은 5초임(r3/policy.py:54, 65, R3_POLICY_CHECK_S). 판매 훅은 policy_arms.json에서 시험군에 적용할 정책 값을 읽음(seller.py:530-539 deal_policy), 구매는 주문마다(r3/hooks/market_buyer.py:676-685 P.load), 재입고는 실행마다(r3/hooks/restock.py:213-218 P.load). |
| 변경 적용 커널 → 정책 파일 ("정책 값") | operator.py:1427-1450 → experiments.py:742-748 → policy_edit.py:549 apply_many, 376-385 _swap; 시험군에 적용할 정책 값은 experiments.py:561-570 |
| 변경 적용 커널 → 규칙 훅 (규칙 코드, 시험 통과 뒤) | code_edit.py:1-14, r3-attempt2/live/seller_restart.sh |
| 운영 LLM → 변경 적용 커널 (done) | operator.py:228-248 COMPACT_SCHEMA, 1394 _finish_compact_locked |
| 컨텍스트 조립 → 운영 LLM 층 전체 ("프롬프트 (운영 LLM 전체)") | operator.py:1067-1126 (역할 운영자), analyst.py:4-6 (조사원이 같은 루프를 씀), agents.py:150-187 (위임 에이전트) |
| 도구함 ↔ 운영 LLM 층 전체 ("도구 호출 (운영 LLM 전체)") | operator.py:749-768 _run_tool, tools.py:948 call |
| 시장 → 컨텍스트 조립 ("순위표 저장본", 실시간 전달이 아님) | 상태 카드가 저장본을 읽음: brief.py:51-64 board_snapshots, 2526-2534 (순위, 점수 위치 절), ledger 도구는 저장본 두 시점을 비교함(tools.py:20-22, 47-56). 저장본을 만드는 수집 스크립트는 배포 호스트에만 있고 이 저장소에는 없다. 저장소에서 확인할 수 있는 것은 저장본을 읽는 쪽(brief.py:51-64)이다. 선은 시장 상자 아래 변에서 출발해 가게 운영자와 구매 운영자 사이를 지나 컨텍스트 조립 상자에 닿음 |
| 변경 적용 커널 → 컨텍스트 조립 ("실험·코드 판정") | operator.py:1108 ex.lines(실험 판정)과 1097 code_edit.lines(코드 수정 판정)를 상태 카드에 넣음 (_wake_compact 안) |
| 검색용 기억 → 공용 도구함 ("recall 검색 결과") | tools.py:459-467 recall, r3-attempt2/kernel/memory.py. 상태 카드에는 기억 절이 없어 도구함으로 이음(brief.py:2526-2544) |
| 실행 기록 → 과정 감사 (읽음) | audit.py:3 (trajectory.jsonl과 실험 장부를 읽음) |
| 과정 감사 → 감사자 | auditor.py:1-6 (audit 수치를 읽음) |
| 감사자 → 운영 기반 그룹 경계 ("지침·도구·카드 구성") | 지침은 컨텍스트에 들어감(auditor.py:1034-1068 _apply_guidance, operator.py:1093-1125), 도구 숨김은 도구함(auditor.py:1076-1116 _apply_tools), 삭제는 운영자가 만든 q_ 도구만(auditor.py:1140-1156), 카드 구성은 상태 카드(auditor.py:221 card_off, operator.py:1098). 에이전트 관리(auditor.py:521-539 _apply_agents)는 감사자 상자 안에 적음 |
| 사람 → 컨텍스트 조립 ("설명서") | operator.py:1105 tools.guide_always() → tools.py:526-536 (목차, 1절, 7절), tools.py:477-485. 선은 운영 LLM 영역의 구매 운영자와 조사원 사이를 지나 컨텍스트 조립 상자에 닿음 |
