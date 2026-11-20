## What I built

WEEK 07 시장에서 GitHub `kcwarni`로 운영하는 서명 영수증 seller와 기억·공개 후기 기반 market buyer를 구현했습니다. 최저가와 구매 예산을 코드로 지키고, 시장 키트가 검증한 실제 Round 2 구매를 기록했습니다. Windows PC와 Cloudflare Quick Tunnel에서 가게·buyer를 운영합니다.

## What I tried and discarded

가격을 두 차례 조정하고 전후 평균 판매가·점유율·구매율을 기록했습니다. 구매율 상승과 별점 하락이 함께 관측되었으며 여러 조건이 달라 인과 효과로 주장하지 않습니다. 별점 약속 할인 이벤트는 실제 참여가 없어 종료하고, 별점과 무관한 구매 후 1개월 이내 불만족 전액 환불 안내·서명 보증·신청 접수로 교체했습니다. 실제 지급은 본인 확인 후 외부 수동 처리입니다.

챌린지 2는 Round 0의 maple-home 거래에서 합의 234·영수증 269·예산 250의 불일치를 원본으로 확인했습니다. 이후 공개 후기 경고를 감지해 해당 가게를 피한 실제 선택 기록을 보존했습니다. 과거 사건을 현재 기억으로 자동 이관하거나 직접 경험 기반 차단을 한 것으로 설명하지 않습니다. 서명·검사·연결 복구 중 실패와 수정 기록도 보존했습니다. 자세한 수치·한계·증거 경로는 `REPORT.md`에 있습니다.

## How to run

Python 3.10 이상과 `requirements.txt` 의존성이 필요합니다. 협상 하네스는 Claude Code + Haiku이며 CLI 로그인 상태가 필요합니다. 키·owner claim code는 제출하지 않습니다. 공식 검사 실행 시 로컬 `shop_key.pem`이 필요합니다.

```powershell
python submissions/24522014/week-07/verify_signed_market.py
python scripts/check_week07.py submissions/24522014/week-07 --github kcwarni
```

운영 시 기존 키를 유지하고 GitHub 연결용 `MARKET_OWNER`를 프로세스 환경으로 제공한 뒤 다음을 실행합니다. 키트는 PC가 켜진 동안 계속 실행해야 하며 자동 재시작은 설정하지 않았습니다.

```powershell
python submissions/24522014/week-07/run_market.py --tunnel
```

## Checklist

- [x] 현재 공식 market test 결과 25/25 PASS 및 `test-report.json` 보존
- [x] `check_week07.py --github kcwarni` 로컬 통과
- [x] 공식 테스트·실제 시장·챌린지 증거를 `logs/`에 보존
- [x] 개인 키와 환경 파일은 Git 추적 대상에서 제외
- [x] 실패 기록과 커밋 이력 보존, squash/rewrite 없음

브랜치의 기존 이력을 유지해 base 대비 변경에는 본인의 이전 WEEK 06 제출물도 포함됩니다. WEEK 06 구조 검사도 함께 통과했으며 이번 작업에서 해당 제출물은 수정하지 않았습니다. 모든 변경 경로는 `submissions/24522014/` 내부입니다.

가게·buyer 활성 상태와 R2 유효 구매를 점검했습니다. 마감까지 가동 유지가 필요하며 점검 시점 상태는 연속 가동 보장이 아닙니다.
