# SETUP Step 3: 혼자 한 번 거래해 보기

2026-10-20, 학번 24522014 (kcwarni), week-07 브랜치.

Step 1 가상환경의 Python을 PATH에 추가하고 저장소 루트에서 실행했다.

```powershell
$env:VIRTUAL_ENV = (Resolve-Path ./submissions/24522014/week-07/.venv).Path
$env:PATH = "$env:VIRTUAL_ENV\Scripts;${PWD}\submissions\24522014\week-07\tools;$env:PATH"
$env:PYTHONUNBUFFERED = '1'
python scripts/trade_test_week06.py submissions/24522014/week-07
```

결과: **21/21 passed -> PASS**, 종료 코드 0. 기존 코드는 수정하지 않았다. seller와 buyer 모두 Claude Code + Haiku를 사용했다. 실제 하네스 호출 39회가 모두 정상 종료했다.

| 검사 | 통과 | 관찰 |
|---|---:|---|
| seller_card / seller_opens / seller_terminal | 3/3 | 카드 응답, 첫 가격 제시, 종료된 task 추가 요청 거절 |
| seller_deal | 3/3 | lamp 판매가 110, 115, 120 |
| seller_holds_reserve | 3/3 | bookshelf 최저가 260 미만 거래 거절 |
| buyer_deal | 3/3 | lamp 구매가 105, 100, 105 (예산 120) |
| buyer_holds_budget | 3/3 | bookshelf 제안은 최대 230, 거래 취소 |
| buyer_protocol_deal / buyer_protocol_holds_budget | 6/6 | A2A-Version 1.0, 후속 요청 taskId 전달 |

`test-report.json`의 전체 통과 여부, 현재 trade test의 SHA-256, `agent.json`의 실행 명령 일치를 확인했다. `logs/setup-step3-report-verification.log`에 확인 결과를 남겼다.

실행 출력은 `logs/setup-step3-trade-console.log`, 공식 거래 로그는 `logs/trade-test.log`다. seller, buyer와 하네스의 원본 실행 로그도 `logs/` 아래에 보존했다.

이 결과는 SETUP Step 3의 week-06 거래 테스트다. week-07 market test와 시장 등록은 이후 단계이며 아직 진행하지 않았다.
