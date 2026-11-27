# Week 08 제출 체크리스트 — 25512085

기준: 2026-11-13. **미완료 / PR 개설 여부 미확인.**
마감은 **2026-11-17(화) 23:59, PR을 연 시각 기준**이다.
[한국어 과제](../../../week-08.html) 736–766행과 [영어 과제](../../../week-08-en.html) 730–760행을 대조했다.

## 산출물 및 증거

- [x] 기존 REPORT 본문을 [prior-report.md](history/20261113-report-figure-01/prior-report.md)에 이동 보존. byte 단위 Git 원본 대조는 메인 검증 대기.
- [x] [REPORT.md](REPORT.md): 구현40/구조30/Players30, 운영/로컬 차이, 모델 한계, 증거 범위 명시.
- [x] [architecture.tex](architecture.tex): Mermaid가 아닌 직접 TikZ/LaTeX 원본, AI 초안 표시.
- [x] 메인 `Get-Command` 결과(2026-11-13 handoff): `pdflatex`, `xelatex`, `lualatex`, `tectonic` 모두 **현재 PATH에서 미발견**. 시스템 전체 미설치라고 단정하지 않음.
- [ ] **현재 PDF 미빌드.** 기존 설치 엔진의 허용된 경로가 확인될 때만 `-no-shell-escape -halt-on-error`로 고유 history/build 경로에 빌드. 패키지 자동 설치·다운로드 금지. 부족하면 설치 없이 중단.
- [ ] 성공한 실제 LaTeX PDF만 최종 `architecture.pdf`로 보관(현재 미산출). ReportLab PDF로 대체하지 않음.
- [ ] PDF 1페이지, 텍스트 추출, 폰트, 렌더 이미지의 글자·화살표·잘림 검증(현재 미실행).
- [ ] 배포 컴퓨터의 **최신 전체 원본** `logs/market-kit.log` 확보 및 민감정보 처리 판단(별도 승인). 기존 11월5일 파일/각4 MiB tail excerpt는 대체 증거가 아님. 기존 로그 편집·tidy 금지.
- [ ] 거부 사유 원문 줄과 제출 시점 board 관측을 별도 새 증거에 보존. 현재 보고서는 11월13일 중간 snapshot.
- [ ] 최종 제출 코드와 배포 코드 차이 해결 방안 승인. [allowlist manifest](history/20261113-submission-alignment-01/runtime-manifest.json) 및 [runtime-source](history/20261113-submission-alignment-01/runtime-source/)는 증거 사본이며 현재 top-level 코드 동일성을 보장하지 않음.
- [ ] [6개 내용 차이/VM 부재7개](history/20261113-submission-alignment-01/pending-differences.md)를 학생이 검토. **추가 승인 없이 로컬 전체를 VM로 덮어쓰기/재배포 금지.**
- [ ] 정확한 자체 파일만 stage, 작업 단위 commit, 기존 실패 commit 보존. squash/rewrite/push하지 않음(이번 문서 담당은 Git 실행하지 못함).
- [ ] 실제 PR 개설 시각과 `[week-08] 25512085` 제목 확인. 최종 Players 점수는 Round 3 종료 board 기준.

## 학생이 직접 이해·수정·설명할 항목

- [ ] 그림의 두 seller 역할(우리 seller/상대 seller), receipt와 실제 정산, 키트/자체 코드 경계를 설명한다.
- [ ] AI 그림 초안을 직접 검토·수정하여 “직접 그린” 요구를 확인한다. AI 사용 사실을 숨기지 않는다.
- [ ] 규칙 기반 Round 3 정책과 legacy 모델 문자열, 개발 AI를 구분한다. 모델 부품 평가 해석을 확인한다.
- [ ] 생성 TTL90초/committed120초와 미배포5분 영속 예약, ACP 세션 보존을 구분한다.
- [ ] hub/file memory/journal/working memory의 업데이트·상한·unknown과 fail-open을 설명한다. seller_history는 legacy, 현행 seller는 hub/cash policy, buyer는 memory.json을 직접 읽는다.
- [ ] restock 수급창 예산 기록, 공개시장 비율 cache, 협상 unit 예약을 구분한다.
- [ ] 3SKU의 무제약 C 우선 선택과 A72/B6/C0 명시조건, ExpectedSaved prior와 공식 Saved를 설명한다. goods 표본5개 이상은 보증 무관 ExpectedSaved>0.25, 미성숙은 >0이며 mature goods67%/food66% cap은 예산·중앙가로 더 낮아질 수 있다.
- [ ] open mandate 테스트 코드와 실행 증거를 구분하고, kit SKU 교체 시 개인 할인/risk cap 미해결을 설명한다.
- [ ] 17/46, student_pct .644, 전체 Players total과 학생 total의 분모/필드를 구분한다.
- [ ] 부분 로그 건수를 누적 성과·실패율로 제시하지 않는다. 오프라인 테스트/배포/효과의 인과관계를 분리한다.

## 메인에게 넘긴 검증

`weeks/week-08/README.md`는 이 checkout에 없었다. 기존 evidence는 `scripts/check_week08.py`도 없다고 기록한다. week07 checker로 week08 통과를 주장하지 않는다. 메인은 현 파일 존재 여부만 재확인하고, 문서 diff/path/link/숫자·원문 보존 검증과 LaTeX/PDF 검증을 별도로 수행한다. 키/env/pem/운영 state를 읽거나 네트워크를 사용할 필요는 없다.
