# 아키텍처 그림 대응표 (architecture.tex의 상자와 화살표 → 코드 위치)

- 경로는 submissions/25520013/week-08 기준이다. 최종 시스템은 system/, 배포한 키트는 kit/market_kit.py이고, 과목 키트 원본은 과목 저장소의 scripts/market_kit.py이다
- 그림 파일: architecture.tex, architecture.pdf(폴더 맨 위)
- 상자 테두리: 굵은 실선은 키트 기본형에 없는 부분, 가는 실선은 키트와 시장 그대로, 겹선은 사람이 쓰는 시장 설명서, 점선과 흰 채움은 모델 거래 경로이다. 모델 거래 스위치를 켜면 새 거래를 맡으며, 마감 직전 기록에는 이 경로가 거래를 처리한 내역이 없다. 이 구분은 상자에만 적용하고 화살표는 모두 같은 굵기이다
- 실행 기록은 운영 층이 남긴 trajectory.jsonl(127,868행, 46 MB)이며, 용량 때문에 제출하지 않았다
- 빌드: XeLaTeX(kotex, TikZ)와 글꼴 Apple SD Gothic Neo가 필요하다
- 색은 층을 나타낸다(시장과 키트 회색, 거래 경로 주황, 운영 LLM 파랑, 운영 기반 청록, 메모리 보라, 메타하네스 빨강, 사람 갈색)
- 상자 문구는 짧게 두고, 조건과 예외는 아래 표의 "그림에서 뺀 조건" 칸에 적었다

## 상자

| 상자 | 테두리 | 코드 위치 | 그림에서 뺀 조건, 확인하지 못한 점 |
| --- | --- | --- | --- |
| 시장 | 가는 실선 | 일정: 과목 저장소 week-08.html:288(Round 3 11-03 시작, 11-17 23:59 종료). 키트: 과목 저장소 scripts/market_kit.py:1-20 | 없음 |
| 키트 v4 ("과목 제공, 차단 구매자 거절만 더함") | 가는 실선 | 과목 저장소 scripts/market_kit.py:1-39(agent.json 훅 계약), 80 KIT_VERSION, 314 AcpShop, 887 restock; 배포한 키트 kit/market_kit.py:315-376, 425-436(차단 구매자 거절), system/tools/block_poller.py | 과목 키트와 다른 곳은 차단 구매자 거절 부분뿐이다. 구매자가 차단 목록에 있고 결제 요청의 주문 번호가 서명된 두 위임장에 적힌 번호와 일치하면 판매를 거절한다. 확인 중 오류가 나면 판매한다 |
| 규칙 훅 (system/hooks/), 상자 문구 "판매, 구매, 재입고, 후기 훅", "정책 값을 읽고 거래를 결정" | 굵은 실선 | 훅 연결 system/agent.json:3-6. 후기 훅: system/hooks/review.py:9-33(결제된 거래의 별점과 후기). 정책 로딩과 결정: system/hooks/seller.py:577-584, 610-626; system/hooks/market_buyer.py:676-685, 726; system/hooks/restock.py:213-218 | 판매 훅은 구매자가 낸 가격을 규칙으로 읽지 못하면 설정에 따라 system/llm/live_v120c.py에 해석을 요청한다(system/hooks/seller.py:621-624, 682-686, 설정 system/llm_config.json). 배포한 설정에서 이 기능은 켜져 있다 |
| 정책 파일 (policy.json: 정책 값 / policy_arms.json: 시험군에 적용할 정책 값, 두 파일) | 굵은 실선 | 쓰는 길: system/kernel/operator.py:1427-1450 _apply_policy → system/kernel/experiments.py:742-748 → system/kernel/policy_edit.py:549 apply_many, 376-385 _swap. 시험군 저장: system/kernel/experiments.py:90 ARMS_FILE, 561-570. 판매 훅은 policy_arms.json에서 시험군에 적용할 정책 값을 읽는다: system/hooks/seller.py:530-539 deal_policy | 동시 비교의 시험군 값은 policy.json 안이 아니라 별도 파일 policy_arms.json에 있다. 두 파일은 운영자가 실행 중에 고치므로, 제출한 system/policy.json은 초기값이고 마감 때의 값은 아니다 |
| 모델 거래 경로 (훅에 연결; 상자 문구 "모델 거래 경로 (훅에 연결)", "모델 거래 스위치를 켜면 새 거래를 맡음", "마감 직전 기록에 거래 처리 내역 없음") | 점선 | system/kernel/seller_bridge.py:1-20, 29 FLAG; system/kernel/agent.py:1-12; system/kernel/buyer_exec.py:1-12; 훅의 분기 system/hooks/seller.py:37-41, 570-571, system/hooks/market_buyer.py:936-939, system/hooks/restock.py:256-267 | 모델 거래 스위치(system/kernel/seller_bridge.py:29 FLAG)는 새 판매만 모델 경로로 보내고, 이미 열린 판매는 원래 경로로 이어진다(system/kernel/seller_bridge.py:241-243 wants, 332-354 _open). 구매와 재입고도 같은 스위치를 쓴다(system/kernel/buyer_exec.py:353-363). 마감 직전 구간(11-17 22:08~23:59)의 실행 기록에는 이 경로가 거래를 처리한 내역(모델 거래 결정 행과 구매 실행 행)이 없다 |
| 운영 LLM 영역 ("gpt-6.1-terra, 기본 추론 강도 medium") | 굵은 실선 | system/kernel/llm.py:21 MODEL, 101-120 build_cmd; system/live/a2ctl.sh:36 effort medium | 마감 직전 구간(11-17 22:08~23:59)의 모델 호출은 모두 gpt-6.1-terra이고, 운영자 medium, 기억 서버 low, 감사자 medium이다. 판매 가격 해석도 gpt-6.1-terra, medium이다(system/llm_config.json:3-6). 이 호출은 별도 파일에 기록된다(system/llm/codex_client_v4.py:148-149, 232-237) |
| 가게 운영자, 구매 운영자 (기본 20분마다 점검, 예약 시각이나 사건에 맞춰 점검) | 굵은 실선 | system/kernel/operator.py:1-14, 100 ROLE_INTERVAL_S, 102 SHOP_LEAD_S, 1020-1066 _wake_reason_compact, 291 role_schema; system/live/a2ctl.sh:33-36 | 가게 운영자는 재입고 가능 시간대 시작 5분 전에도 점검한다(102 SHOP_LEAD_S 300). 이것은 운영자가 다음 점검을 예약하지 않았고 재입고 가능 시간대 시작을 추정할 수 있을 때의 기본값이다(system/kernel/operator.py:931-942 _window_reason, 1040-1053). 사건은 실험 판정과 순위 급변이다(1020-1066). 마감 직전 구간에 가게 13번, 구매 23번 점검했고, 대부분 운영자의 다음 점검 예약으로 시작했다 |
| 조사원 ("질문 하나를 여러 단계로 조사해 결과를 기록") | 굵은 실선 | system/kernel/analyst.py:1-15, 100 Analyst; system/kernel/questions.py:121 ask, 200 add_finding; system/live/a2ctl.sh:40-41 | 실행 스크립트의 기본 시작 목록(system/live/a2ctl.sh:27)에 조사원이 없으므로, 조사원은 이름을 지정해 시작한다. 실행 기록에는 마감까지 조사원 실행이 280회 남아 있고, 마감 직전 구간에는 조사원 실행과 조사원의 모델 호출이 각각 0건이었다 |
| 위임 에이전트 | 굵은 실선 | system/kernel/agent_registry.py:1-13; system/kernel/agents.py:1-15, 78 CreatedAgent, 253 poll | 마감 때 위임 에이전트 실행기가 돌았는지와 등록된 에이전트 수는 확인하지 않았다 |
| 컨텍스트 조립 ("점검마다 새 컨텍스트", "도구 결과가 길어지면 요약") | 굵은 실선 | system/kernel/operator.py:1067-1126 _wake_compact, 111-114 관찰 예산, 123 COMPACT_AT, 128 COMPACT_MIN_OLD, 129 COMPACT_RECENT, 138 COMPACT_FAILS, 1253-1300 _compact; system/kernel/brief.py:2156 COMPACT_CHARS, 2517-2557 build_compact | 요약 조건: 기존 요약과 남긴 결론, 관찰을 합친 길이가 60,000자(123) 이상이고 최근 2단계를 뺀 이전 부분이 30,000자(128) 이상이며 요약 실패가 2번(138) 미만일 때이다. 최근 2단계는 원문을 유지한다. 길이 계산에는 남긴 결론도 들어가고, 기존 요약은 전체 길이에만 더하고 이전 부분에는 넣지 않는다(system/kernel/operator.py:1260-1271). 실제 프롬프트 크기는 확인하지 않았다 |
| 공용 도구함 ("기본 도구 24개 (숨김 전, recall 포함)") | 굵은 실선 | system/kernel/tools.py:909 RECOMMENDED, 917-945 TOOLS(27개), 794 tool_search, 459 recall, 760 analyze; system/kernel/operator.py:699-716 listed(하네스 도구 3개를 빼 24개); system/kernel/code_tools.py:109 check, 198 execute; system/kernel/made_tools.py:488 add, 533 remove(감사자의 q_ 도구 삭제는 system/kernel/auditor.py:1140-1156) | 마감 전 실행 기록에 조사원이 등록한 q_ 도구 14개, 이 가운데 13개를 합계 365회 호출, 마감 전 삭제 기록 없음 |
| 변경 적용 커널 ("code_edit: 시험, 30분 감시, 이상 시 복구") | 굵은 실선 | system/kernel/operator.py:228-248 COMPACT_SCHEMA, 1394 _finish_compact_locked, 1427 _apply_policy; system/kernel/experiments.py:746-748 → system/kernel/policy_edit.py:549 apply_many; system/kernel/code_edit.py:1-14, 137 WATCH_S 1800, 1477 revert, 1814 watch, 592 protected_error, 634 danger; system/kernel/experiments.py:1-20, 658 apply_policy, 1310 evaluate, 1424 evaluate_split, 1771 tick | 실험 판정 수와 코드 수정 수는 확인하지 않았다 |
| 검색용 기억 memd | 굵은 실선 | system/memd/server.py:1-14, 34 EMBED_MODEL, 56 build_memory, 107 MemService; system/kernel/memfeed.py:1-20, 273 run_once; system/live/a2ctl.sh:31(300초); system/kernel/memcheck.py:1-20, 205 run | 기억 저장 3,428번, 추출 사실 13,059개, 검색 20,430번. memcheck는 운영자가 done에 요청할 때만 실행되며(system/kernel/operator.py:1763-1764) 마감까지 실행 기록이 없다. qdrant에 남은 기억 수는 확인하지 않았다 |
| 실행 기록 ("추가만"은 trajectory.jsonl과 findings.jsonl에만) | 굵은 실선 | system/kernel/trajectory.py:1-19; system/kernel/experiments.py:44 LEDGER; system/kernel/questions.py:1-15(findings.jsonl); system/kernel/operator.py:1302-1313 _hand_over | experiments.json은 실험 상태를 고쳐 다시 쓰는 파일이라 "추가만"에서 뺐다 |
| 과정 감사 | 굵은 실선 | system/kernel/audit.py:1-13 | 없음 |
| 감사자 ("기본 1시간 또는 판정 3건", "실수 증가 기준을 넘으면 지침 복구") | 굵은 실선 | system/kernel/auditor.py:1-13, 60 INTERVAL_S, 61 MIN_GAP_S, 62 VERDICTS_TRIGGER, 63 MIN_WAKES, 381-383 _interval_s, 400-410 due, 421-449 judge_guidance, 1029 tells_value, 1034 _apply_guidance, 1076 _apply_tools, 872 _lifecycle_block, 1234 run, 1344 maybe_run; 운영자 프로세스에서 부름 system/kernel/operator.py:1835 | 판정 3건 경로에도 마지막 실행 뒤 최소 30분 간격(61)이 있다. 지침 복구는 변경 전후 점검이 각각 4번(system/kernel/auditor.py:63 MIN_WAKES) 이상 쌓인 뒤, 점검당 실수 수가 변경 전 값의 1.25배에 0.5를 더한 값보다 크면 한다(64-67, 421-449). 마감까지 감사자 기록 85건(감사 완료 84, 건너뜀 1), 지침 변경 11건, 되돌림 1건 |
| 사람 (MARKET_BRIEF.md, "시장 규칙, 목표, 안전 기준, 확인 사실") | 겹선 | 파일: system/MARKET_BRIEF.md(system/kernel/tools.py:477 GUIDE_PATH). 운영자에게 들어가는 길: system/kernel/operator.py:1105 tools.guide_always() → system/kernel/tools.py:526-536(설명서 목차, 1절, 7절 전문; 나머지 절은 guide 도구로 읽음), system/kernel/tools.py:477-485 _guide_sections | 없음 |

## 화살표

화살표는 모두 같은 굵기로 그렸다.

| 화살표 | 코드 위치 |
| --- | --- |
| 시장 ↔ 키트 (요청, 주문, 공급, 정산) | 과목 저장소 scripts/market_kit.py:1-20 |
| 키트 → 규칙 훅 (훅 호출: 판매, 구매, 재입고, 후기) | system/agent.json:3-6, 과목 저장소 scripts/market_kit.py:887-899 restock |
| 정책 파일 → 규칙 훅 ("훅에서 정책 적용", 규칙 훅 상자 동쪽 직선 테두리에 닿음) | 판매는 새 주문 때 정책을 확인한다(system/hooks/seller.py:577-584, refresh_policy 343; 열린 거래는 연 때의 정책 유지). 파일 재확인의 기본 간격은 5초이다(system/policy.py:54, 65, R3_POLICY_CHECK_S). 판매 훅은 policy_arms.json에서 시험군에 적용할 정책 값을 읽고(system/hooks/seller.py:530-539 deal_policy), 구매는 주문마다(system/hooks/market_buyer.py:676-685 P.load), 재입고는 실행마다(system/hooks/restock.py:213-218 P.load) 읽는다 |
| 변경 적용 커널 → 정책 파일 ("정책 값") | system/kernel/operator.py:1427-1450 → system/kernel/experiments.py:742-748 → system/kernel/policy_edit.py:549 apply_many, 376-385 _swap; 시험군에 적용할 정책 값은 system/kernel/experiments.py:561-570 |
| 변경 적용 커널 → 규칙 훅 (규칙 코드, 시험 통과 뒤) | system/kernel/code_edit.py:1-14, system/live/seller_restart.sh |
| 운영 LLM → 변경 적용 커널 (done) | system/kernel/operator.py:228-248 COMPACT_SCHEMA, 1394 _finish_compact_locked |
| 컨텍스트 조립 → 운영 LLM 층 전체 ("프롬프트 (운영 LLM 전체)") | system/kernel/operator.py:1067-1126(역할 운영자), system/kernel/analyst.py:4-6(조사원이 같은 루프를 씀), system/kernel/agents.py:150-187(위임 에이전트) |
| 도구함 ↔ 운영 LLM 층 전체 ("도구 호출 (운영 LLM 전체)") | system/kernel/operator.py:749-768 _run_tool, system/kernel/tools.py:948 call |
| 시장 → 컨텍스트 조립 ("순위표 저장본", 실시간 전달이 아님) | 상태 카드가 저장본을 읽는다: system/kernel/brief.py:51-64 board_snapshots, 2526-2534(순위, 점수 위치 절). ledger 도구는 저장본 두 시점을 비교한다(system/kernel/tools.py:20-22, 47-56). 저장본을 받아 오는 스크립트는 배포 컴퓨터의 시작 스크립트에 있고 제출하지 않았다. 선은 시장 상자 아래 변에서 출발해 가게 운영자와 구매 운영자 사이를 지나 컨텍스트 조립 상자에 닿는다 |
| 변경 적용 커널 → 컨텍스트 조립 ("실험·코드 판정") | system/kernel/operator.py:1108 ex.lines(실험 판정)과 1097 code_edit.lines(코드 수정 판정)를 상태 카드에 넣는다(_wake_compact 안) |
| 검색용 기억 → 공용 도구함 ("recall 검색 결과") | system/kernel/tools.py:459-467 recall, system/kernel/memory.py. 상태 카드에는 기억 절이 없어 도구함으로 잇는다(system/kernel/brief.py:2526-2544) |
| 실행 기록 → 과정 감사 (읽음) | system/kernel/audit.py:3(trajectory.jsonl과 실험 장부를 읽음) |
| 과정 감사 → 감사자 | system/kernel/auditor.py:1-6(감사 수치를 읽음) |
| 감사자 → 운영 기반 그룹 경계 ("지침·도구·카드 구성") | 지침은 컨텍스트에 들어간다(system/kernel/auditor.py:1034-1068 _apply_guidance, system/kernel/operator.py:1093-1125). 도구 숨김은 도구함(system/kernel/auditor.py:1076-1116 _apply_tools), 삭제는 운영자가 만든 q_ 도구만(system/kernel/auditor.py:1140-1156), 카드 구성은 상태 카드(system/kernel/auditor.py:221 card_off, system/kernel/operator.py:1098)에 적용한다. 에이전트 관리(system/kernel/auditor.py:521-539 _apply_agents)는 감사자 상자 안에 적었다 |
| 사람 → 컨텍스트 조립 ("설명서") | system/kernel/operator.py:1105 tools.guide_always() → system/kernel/tools.py:526-536(목차, 1절, 7절), system/kernel/tools.py:477-485. 선은 운영 LLM 영역의 구매 운영자와 조사원 사이를 지나 컨텍스트 조립 상자에 닿는다 |
