# R2 표시 확인

2026-10-20 21:31 KST에 실제 서버를 조회했다.

- `/api/stats`의 현재 라운드는 2이며 `rounds`에 `0`, `1`, `2`가 모두 있다. R2 가게 목록에도 `kcwarni`가 있다.
- `/api/board?round=2`에서 가게와 buyer 모두 `alive: true`, GitHub 소유자는 `kcwarni`이다.
- 당시 R2 가게 판매는 0건, 방문 1건, 후기 0건이었다. buyer는 유효 구매 1건이었다. 서로 다른 실적이다.
- 메인 HTML은 `/api/stats`의 `rounds` 키로 R0/R1/R2 탭을 만들고 현재 라운드 또는 사용자가 선택한 라운드를 보여 준다. 3초마다 갱신한다. R1 실적은 R2로 합산하지 않는다.

따라서 확인 시점의 서버에서 R2 등록 누락은 재현되지 않았다. 사용자의 브라우저 화면이나 캐시는 직접 확인하지 않았으므로 원인으로 단정하지 않는다. R2 탭 자체가 보이지 않으면 강력 새로고침(Ctrl+F5) 후 확인한다.

근거는 수정하지 않은 응답 스냅샷 `logs/review-event/r2-recheck.json`, `logs/review-event/homepage-stats.json`이다. 이후 상태는 실시간으로 달라질 수 있다.
