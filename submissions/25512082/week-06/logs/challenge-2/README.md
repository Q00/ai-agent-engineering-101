# Challenge 2 실행 로그

`actual-01/`에는 2026-10-20 실제 실행의 원본 출력이 있다.

- `buyer.log`: 쇼핑 목록 전체의 JSON 결과
- `seller-19001.log`부터 `seller-19020.log`: Seller 20개의 개별 원본 출력

실행한 쇼핑 목록은 `desk lamp`, `office chair`, `bookshelf`이고 총예산은
900이다. 세 거래의 합계는 605이며 서로 다른 task ID로 모두
`TASK_STATE_COMPLETED`가 되었다. 거래가 배정되지 않은 Seller 로그는 원본
stdout/stderr가 없으므로 빈 파일로 보존한다.
