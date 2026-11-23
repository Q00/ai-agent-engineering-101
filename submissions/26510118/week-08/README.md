# week-08 중간 평가: Kiro729 (26510118)

Round 3 시장에 가게와 buyer를 올린 에이전트다. 결과는 Players 표 Class 보기 46명 중 6위(백분위 0.889).

## 제출물

| 제출물 | 위치 |
|---|---|
| 보고서 | `REPORT.md` (3부는 `make_report.py`가 `logs/`에서 생성) |
| 아키텍처 figure | `architecture.tex`, `architecture.pdf` (한 페이지) |
| 바꾼 코드 | `round3/` (Round 3에서 실제로 돌린 코드, 모듈 지도는 `round3/README.md`) |
| 키트 로그 | `logs/market-kit.log`, `logs/market-kit.log.1` (정산 줄 포함) |

## 폴더 안내

| 경로 | 내용 |
|---|---|
| `round3/` | Round 3 에이전트. 11/3부터 끝까지 서버에서 돌린 것은 이 폴더뿐이다 |
| `logs/` | 서버 로그를 고치지 않고 옮긴 것: 키트 로그 두 개, `analysis.log`(분석 실행기), `manual_runs.log`(서버에서 손으로 한 일), `seller_restarts.log` |
| `logs/round3-final/` | 종료 직전과 직후에 저장한 board(`2358`, `0000`, `0005`), 20분마다 남긴 우리 board 숫자(`board_history.jsonl`) |
| `round3/logs/` | 배포 전 테스트 결과 |
| `figures/` | 시스템이 어디서 도는지 그린 Archify 그림(`system-overview.html`, 미리보기 `.png`, 정의 `.json`) |
| `make_report.py` | REPORT.md 3부 생성기 |

키트 로그는 20 MB가 넘으면 `.log.1`로 넘어가고 그 전의 `.1`은 지워진다. 그래서 남은 로그는 11/16 05:21
KST부터 끝까지다. 라운드 전체 숫자는 종료 board에서 읽었다(REPORT.md 3-2).

맨 위의 나머지 파일(`acp.py`, `buyer.py`, `seller.py`, `shop_core.py`, `buyer_core.py`, `market_buyer.py`,
`worker/` 등)은 10/27~10/29에 week-07 가게와 buyer를 옮겨 와서 처음 Round 3 계획(상품 3개, 정해진 하한가)으로
만든 것이다. 키트 v4의 시장(재고, SKU, 하한 없음)이 공개되면서 그 계획을 버리고 `round3/`를 새로 만들었다.
지운 실험도 기록이라 남겨 두었고, 시장에서는 돌지 않았다. `worker/`의 Cloudflare 가게는 10/29부터 멈춰 있다.

## 다시 만들기

```bash
python make_report.py
```

`REPORT.md`의 두 표시 사이(3부)만 다시 쓴다.

```bash
pdflatex architecture.tex
```

`architecture.pdf`를 다시 빌드한다(tikz, helvet, amssymb, geometry).

API 키와 개인키는 커밋하지 않았다. 가게와 buyer의 개인키(`*.pem`)는 `.gitignore`로 막았고, OpenAI 키는
서버의 환경 파일에만 있었다.
