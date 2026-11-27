# 문서·그림 작업 handoff (2026-11-13)

## 최신 revision 2 — 독립 review 반영

REPORT/architecture.tex/SUBMISSION_CHECKLIST 및 이 handoff만 수정했다. `prior-report.md`와 기존 운영 증거·소스·로그는 변경하지 않았다.

- 운영 buyer1454–1460을 다시 읽어 mature(정산 표본5개 이상) goods의 ExpectedSaved>0.25가 보증 유무와 무관함을 반영했다. 미성숙은 >0. mature goods67%/food66% cap과 예산·중앙가 최솟값을 구분했다. 불필요한 board Saved .6615 비교를 삭제했다.
- seller_history는 legacy 경로로 분리하고 현행 seller hub/cash policy, buyer memory.json 직접 입력을 명시했다.
- 그림을 협상/결제/원장 ingestion/도구출력의 수평 구획으로 재구성했다. buyer kit ACP client→상대 shop ACP server→market settle을 분리하고 restock→store 공개 원장 직접 GET→hub 경로를 반영했다. 반환 계약 buyer `{shop,receipt,accept,...}`, restock `{buy:[...]}`, review `{stars,review}`를 표시했다.
- 로컬 catalog cache 계약은 `mtime_ns+size`이며 같은1초 TTL이 아님을 명시했다.
- **메인 전달 실행 결과:** `Get-Command`에서 pdflatex/xelatex/lualatex/tectonic 모두 현재 PATH 미발견. 시스템 전체 미설치 주장은 하지 않는다. PDF는 미빌드/미산출이며 1페이지·폰트·텍스트·렌더 검증 미실행이다. 아래 초기 handoff의 “엔진 탐색 미실행”은 당시 이 문서 담당의 상태이고 현재 메인 결과는 이 절을 우선한다.
- 문서 담당은 이번에도 파일 읽기/편집만 수행했다. shell/network/build/commit 실행 없음. 도식은 레이아웃을 단순화했으나 실제 렌더 검증 완료가 아니다.

## 작성 범위

문서 편집 승인에 따라 REPORT 원문을 `prior-report.md`로 이동 보존하고 최상위 REPORT, architecture.tex, SUBMISSION_CHECKLIST를 작성했다. source/log/state와 기존 history는 수정하지 않았다. PDF skill을 로드했다. 원문 이동은 patch의 Move 기능이며 제목을 동일 문자열로 둔 no-op hunk만 사용했다. **byte/CRLF 동일성은 메인이 Git 원문과 대조해야 한다.**

확인 자료: 루트 AGENTS(대화 제공), week-08.html736–766, week-08-en.html730–760, 기존 REPORT, 운영 evidence README/summary/structure/pending/manifest/metrics, 필요한 운영 소스와 Saved/배포/로컬 예약 이력. 원본 운영 데이터·키·env·pem은 읽지 않았다. 네트워크·VM·설치·다른 에이전트 호출 없음.

## 실행 제한 / 정확한 상태

상위 역할 지침이 셸·실행을 금지하므로 Git stage/commit, 엔진 탐색, LaTeX 빌드, PDF검증, 계산·테스트는 **미실행**이다. 엔진 부재로 단정하지 않았다. architecture.pdf 생성 없음. 파일 읽기와 grep, patch만으로 작성했으며 실행 검증 성공을 주장하지 않는다.

메인 실행 요청(설치/네트워크 없이):

1. 원문 `git show`와 prior-report 내용/byte 대조. 새 문서 파일만 경로·diff whitespace 검사. 기존 untracked/사용자 파일 stage 금지.
2. backup은 prior-report만 먼저 commit, REPORT만 별도 commit, tex(성공 시 pdf/build evidence) 별도 commit, checklist/handoff 검증 기록 별도 commit. 원 REPORT 삭제를 backup commit에 stage할지는 메인이 diff를 보고 결정(현재 새 REPORT가 존재). 실제 commit ID는 여기에 발명하지 않음.
3. 설치된 pdflatex/xelatex/lualatex 및 PDF 검사 도구를 확인. 필요한 패키지 이미 존재할 때만 `-no-shell-escape -halt-on-error -interaction=nonstopmode`와 고유 output-directory 사용. 네트워크 자동 패키지 설치 금지. 엔진/패키지 없으면 tex까지만 산출.
4. 성공 PDF만 최종 경로에 복사하고 page count=1, font/text/render 확인. TikZ 화살표 겹침과 긴 레이블을 시각 검토. compile 안 한 소스이므로 문법/레이아웃을 검증 완료라고 하지 않음.
5. 링크/수치 재대조 및 자신의 검증 결과를 새 별도 파일에 남김. 기존 evidence JSON/log는 변경 금지.

## 추가 확인 포인트

- 메인 handoff의 buyer `17e0f24` 대응은 보고서에 handoff 정보로 구분했으며 이 작업에서 Git ancestry를 확인하지 못했다.
- 요청의 “restock2/3”이 무엇을 지칭하는지 지정 근거가 없다. 현재 문서는 확인한 수급창 예산 기록/수요 분류/30분 public ratio fallback cache와 seller 예약을 구분한다. 별도 2/3 수치/주기 요구라면 근거 행을 확인하여 추가할 것.
- 공식 Saved 정의는 과거 step1의 메인 전달 인용이고 정확한 공식 URL이 그 기록에 없다. 외부 조회 금지 때문에 독립 확인하지 않았다. 공식 검증 완료라 주장하지 않는다.
- 학생 AI 초안 검토 및 모델 평가 해석, 최신 VM 전체 로그, 제출 코드 동일성, 제출시점 지표와 거부 원문, PR개설은 미완료다.
- 이번 작성은 Git commit/push/재배포 승인이 아니라 승인된 문서 범위만 수행한 것이다. 실행 가능한 메인이 정확한 파일 단위 commit을 마무리해야 한다.
