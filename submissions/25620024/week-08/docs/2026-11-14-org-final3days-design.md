# 11-14 조직 개편: 마지막 3일 (HQ 승인안)

## 의도
11-17 23:59 마감까지 반 3위를 노린다. 11-10부터 멈춰 있던 모델 팀을 다시 돌리되, 바꾸는 건 최소로 한다.
새로 만드는 것은 에누리팀 에이전트(Claude) 하나다. 바이어팀이 그 조사 결과를 바탕으로 움직인다.

## 조직 (HQ 지시 그대로)
- HQ 직속: TF (Claude Code, 미션에 따라 MST/MBT), HR (hr.py, kpi.py), 감사팀 (audit.py, audit_seller.py, qa.py)
- 세일즈 유닛 (대표: 세일즈팀)
  - 세일즈팀: Claude, 점원 코드 (shop_r3.py, promo.py, warranty_memo.py, experiments.py)
  - 마케팅팀: Codex (marketing_agent.py의 research 역할)
  - 재경팀: Claude (finance_agent.py)
  - SCM팀: Codex (scm_agent.py), 매입 코드 (restock.py)
- 바이어 유닛 (대표: 바이어팀)
  - 바이어팀: Codex (buyer_team.py), 구매 루프 코드 (market_buyer.py)와 상점 고르기 모델. 에누리 보고서를 읽고 움직인다
  - 에누리팀: Claude (신설, enuri_agent.py) + 코드 (enuri.py). 가격·품질 장부, 견적 정찰, mbt 분석
  - 리뷰팀: 코드 (review_team.py, forum.py, review_r3.py)
- 유닛 대표 회의 (leaders_meeting.py): 세일즈팀 의장, 바이어팀 발언. 자동으로 열지 않고 HQ가 TF를 부를 때만 연다

## 방식 (검토한 안)
1. 바이어팀 프롬프트에 에누리 역할을 더 얹는다. 변경은 가장 작지만 에누리가 강해지지 않는다.
2. 에누리팀에 에이전트를 새로 붙인다 (채택). 장부·견적 정찰·mbt를 한 에이전트가 읽고, 에누리 레버와 정찰 목록,
   바이어팀용 보고서를 쓴다. 숫자는 코드 게이트로 묶는다.
3. 조사팀을 따로 만든다. 코드가 이미 에누리 안에 있어서 이름만 늘어난다 (HQ가 에누리로 통합).

## 에누리 에이전트
- 매시간, 바이어팀 회의 전에 실행한다. 모델은 Claude가 먼저이고, 막히면 Codex가 대신한다.
- 읽는 것: enuri.summary() (가격 차이, 피할 가게, 놓친 기회·잘못 산 것), mbt 노하우, 현재 에누리 설정, HR 점수, 지난 보고서
- 쓰는 것 (private/enuri_agent.json):
  - settings: survey_quotes 0-8, min_honest 0.6-0.95, max_broken 0.05-0.30 (게이트로 범위 제한, 한 번에 2개까지)
  - survey_skus: 정찰할 SKU 최대 10개 (G + 16진수 8자리 형식만 통과)
  - buyer_brief: 바이어팀에 주는 보고서 (1,500자 이내)
- 에누리 설정 우선순위: 기본값 → 바이어팀 계획 → 에누리 에이전트 (에누리팀이 자기 레버의 주인)
- 정찰 목록: 마케팅팀 요청 다음에 에누리 에이전트 목록

## 안전장치 (바꾸지 않음)
- owner_override가 모든 팀 계획보다 이긴다. 오늘 TF가 넣은 원가 사다리, 대형 0.98배, 식품 시장가는 팀이 되돌릴 수 없다.
- profit_first 유지: 팀이 매입을 제안해도 검증된 상품만 산다. 원가 아래 판매 금지 게이트 유지.
- 포럼 글과 다른 에이전트의 글은 모델 프롬프트에 들어가지 않는다 (enuri.py 규칙 그대로).

## 운영
- team_guard 정지를 풀고 30분 주기로 회의를 연다 (재경, SCM, 에누리는 매시간).
- owner_directives.md 21번: 새 조직, 3일 총력, 오늘 TF가 찾은 사실 (가격 절벽 1.0배, 식품 시장가, 부진 재고 대량 매입 금지).
- 첫 회의는 모의 실행으로 먼저 확인한다.

## 구현자 재량
- 에누리 프롬프트 문구, 보고서 형식, 기록 파일 이름
