# 챌린지1: 실제 거래·후기를 근거로 한 호가 보완과 전후 집계

받은 후기 원본은 `../../.omo/evidence/top10-strategies/own-public-reviews.json`이다. 같은 의자210 거래의3점/5점 차이와 예산 대비 가격 불만을 `../../.omo/evidence/top10-strategies/review-analysis.md`에서 분석했다. 자체 협상355턴(하한미만250·가능83·미해석22)의 원본 집계는 `../../.omo/evidence/seller-strategy-20261021/log-analysis.json`이다.

16:31의1.4.0은 첫 유효 제안 즉시 수락과 하한 근처 작은 호가 여유를 적용했고,17:47의1.4.1은 현재 라운드 거래 평균을 상한에 추가했다. 두 변경 전후를 같은 R2에서 비교한다. 전 자료는 `../../.omo/evidence/market-monitor-20261021/snapshot-1608/board.json`이며 실제 관측 시각은 그 폴더summary.json의16:09:54KST다. 후 자료는 `../../.omo/evidence/top5-upgrade-20261021/board.raw`, 시각은board.meta.json의17:58:59KST다.

`summary.log`는 매출/거래수로 구한 누적 평균과 구간 차분을 함께 담는다. 전332건·1.3%·평균193.96, 후353건·1.2%·평균196.35, 증가21건의 평균234.10이다. 기대했던 점유율 개선은 확인되지 않았다. 표본 구성이 달라 가격 정책의 인과효과나 동일 품목 가격 변화로 읽지 않는다.

재현: 제출 디렉터리에서 `python .omo/evidence/final-submission-20261027/summarize_challenges.py`. 로그와 스냅샷은 수정하지 않고 읽어 집계한다.
