## What I built

Round 3 시장에서 재고 매입·판매·협상·ACP/AP2 정산을 수행하는 Shop과 Buyer를 만들었고, 공통 사건 메모리와 시장정보·회계/리스크·품질/고객·조달·판매/계약·점수전략·운영통제를 회사 조직 구조로 연결했습니다. 최종 운영은 Shop v34 / Buyer v32의 수치 판단 경로이며, 운영 중 사용했던 LLM Actor–Critic 및 배경 연구·분석 도구 경로도 한 페이지 아키텍처에 구분해 설명했습니다.

- 제출 위치: `submissions/26510121/week-08/`
- 설계와 운영 기록: `REPORT.md`, `SHOP_COMPANY_ORG_2026-11-16.md`, `FINALDAY_DEPLOYMENT_2026-11-16.md`
- 한 페이지 그림: `architecture.tex`, `architecture.pdf`
- 실제 배포·정산 로그: `logs/market-kit.log`, `logs/auto-join-*.txt`, 버전별 release/live/검증 기록
- 최종 설정: `finalday_departments_enabled=true`, `llm_disabled=true`, `research_enabled=false`, `capability_llm_enabled=false`

## What I tried and discarded

- 초기에는 GPT-6 Luna Actor와 GPT-6.1 Terra Critic으로 후보를 검토했습니다. 이후 Main 거래 경로의 지연과 비용을 줄이는 수치 실행을 사용하고, v19에서는 Luna 분석 필요성 판단, Sol 도구 설계·전략 분석, Terra 독립 검토를 배경 경로로 분리했습니다. 최종 운영에서는 유료 LLM 경로를 비활성화했습니다. 모델 구조를 구현했다는 사실과 모델이 최종 수익을 개선했다는 주장은 구분합니다.
- LLM의 불완전 응답, 잘못된 결정 함수 호출, 응답 단계 해석 실패를 겪었습니다. 구조화 출력·후보 ID·기한·독립 검토·수치 fallback 검사를 보완했고, 실패와 수정 전후 검증 로그를 보존했습니다. `V19_MODEL_ROLES_2026-11-12.md`와 v18/v19 로그에 근거가 있습니다.
- 터널은 열렸지만 공개 discovery 또는 시장 재등록이 실패한 시도가 있었습니다. Pinggy/Cloudflare 경로와 재등록·복구를 점검했고, 프로세스가 살아 있다는 것만으로 가게 복구를 성공 처리하지 않도록 했습니다.
- 재고 확대·보증·품질·가격 정책을 여러 차례 시험했습니다. 예측 이익을 실제 성과로 세지 않고, 회계·리스크 근거와 성숙한 품질/환불을 사용했습니다. 조달 복구 후에도 성장·순위 개선이 충분하지 않았고, 폐기·보관·환불 및 경쟁자의 변화가 남았습니다. `V33_PROGRESS_2026-11-17_1240.md`와 v34 기록에 한계를 적었습니다.
- 반올림된 별점의 미세한 예측 변화가 동점 집단 전체의 순위 손실/보너스로 계산되던 문제를 v34에서 교정했습니다. 초기 hold 비율 완화와 실제 판매는 관측했지만 장기 ALL·이익 향상의 인과적 증거로 제시하지 않았습니다.
- 11월 21일 기본 로컬 재검사에서 669개가 통과하고 8개가 실패했습니다. 종료일을 지난 `market_end_at` 때문에 계획·보관료 기간이 0이 되는 시각 의존성이며, 같은 8개는 당시 시각을 사용하는 진단에서 통과했습니다. 진단 통과를 기본 실행 통과로 표시하지 않습니다.

## How to run

저장소 루트의 PowerShell에서 실행합니다. 로컬 검사는 공개 시장에 주문하거나 유료 모델을 호출하지 않습니다.

```powershell
python -m venv submissions/26510121/week-08/.venv
& ./submissions/26510121/week-08/.venv/Scripts/python.exe -m pip install -r submissions/26510121/week-08/requirements-lock.txt
& ./submissions/26510121/week-08/.venv/Scripts/python.exe submissions/26510121/week-08/verify.py
```

- **현재 기본 검사 결과:** 669 passed / 8 failed. 원본: `logs/verification-20261121T053348097594Z.txt`.
- 시각 의존성 진단: `logs/pr-readiness-clock-diagnostic-20261121T054127379377Z.txt` (실패한 8개만 당시 시각에서 8 passed; 운영 코드·설정 변경 없음).
- 최종 운영은 LLM 미사용입니다. 과거 경로의 모델 배정은 GPT-6 Luna, GPT-6.1 Sol, GPT-6.1 Terra이며 버전별 차이는 `REPORT.md`와 `V19_MODEL_ROLES_2026-11-12.md`를 봅니다. LLM 검증을 별도로 선택할 때만 `OPENAI_API_KEY`가 필요합니다. 키 값은 포함하지 않았습니다.
- 과거 공개 배포에는 `MARKET_OWNER`와 로컬 Shop/Buyer 개인키가 필요했습니다. 당시 실행 명령은 아래와 같습니다. Round 3는 종료됐으므로 기본 재현 절차에는 시장 재참가를 포함하지 않습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./submissions/26510121/week-08/join.ps1 -TunnelProvider cloudflare
```

아키텍처는 TinyTeX/TeX Live와 TikZ로 빌드합니다.

```powershell
Set-Location submissions/26510121/week-08
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=artifacts/architecture-build architecture.tex
```

## Checklist

- [ ] `python scripts/check_week08.py submissions/26510121/week-08` passes locally — 현재 upstream에 `check_week08.py`가 없어 실행할 수 없습니다. 대체 로컬 검사 `verify.py`의 결과는 위와 같이 669 passed / 8 failed입니다.
- [x] Run logs are committed under `logs/` — 원본 로그를 보존했습니다. GitHub 단일 파일 제한(100 MiB)을 넘는 미추적 raw snapshot 3개는 로컬에 그대로 두었으며 경로·크기는 `PR_SUBMISSION_MANIFEST.json`에 기록했습니다.
- [x] No API keys anywhere in the diff — 제출 이력의 blob과 공개할 작업 파일에서 키/개인키 형태 및 금지 파일명을 검사했습니다. 추가로 로컬 API 키·owner 자격증명 값을 메모리에서 직접 비교해 일치 0건을 확인했습니다. 탐지된 유사 문자열은 공개 `checkout_hash`의 해시 값으로 확인됐습니다. 휴리스틱 검사의 한계도 검토 기록에 명시했습니다.
- [x] History is not squashed — 실패·수정·검증 커밋을 보존했습니다.

**Draft 검토 사항:** 사용자의 최종 확인을 위해 Draft로 엽니다. `REPORT.md`의 초기 운영 기록을 최종 결과 중심으로 정리하고, 10주차 기준값과 비교한 표 및 종료 시점 공식 Players Class 순위 근거를 보완해야 합니다. `week-08` 브랜치의 기존 이력을 그대로 사용하므로 diff에는 기존 week-07 변경 71개 파일도 포함됩니다. 기본 검사 8개의 시각 의존성 보완도 남아 있습니다. 최종 검토 전 준비 완료로 표시하지 않습니다.
