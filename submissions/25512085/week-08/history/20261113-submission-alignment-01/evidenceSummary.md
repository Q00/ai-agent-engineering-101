# 운영·제출 정렬 증거 요약

## 범위와 버전

- **운영 읽기 및 새 비민감 증거만 수행.** 배포, 재시작, 거래, POST, 패키지 설치, push, 기존 코드·로그·state·보고서·그림 수정 없음.
- 대상: GCP `studious-plate-458511-a1`, `us-central1-a`, VM `market-agent`. 기존 gcloud SSH 인증과 기존 Python `/home/comalswjd/venv/bin/python`만 사용했다. 애플리케이션 모듈을 import하거나 실행하지 않았다.
- VM 제출 디렉터리: `/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08`.
- 최초 운영 관측: **2026-11-12 16:20:45 UTC = 2026-11-13 01:20:45 KST**. 최종 소스 재검증: 16:26:04 UTC.
- VM HEAD: **`6bb3cd855fe76a29e1412699688595069ca5fa45`**. 최종 확인한 tracked dirty 수 0. 실제 존재하는 허용 소스 15개 모두 disk SHA-256 = VM HEAD blob SHA-256, 재검증 시 동일.
- 사용자 지정 로컬 시작 HEAD `5513728f30532c577dee48ba5f42edb86d1c5105` 확인. tracked 수정은 없었으나 미추적 파일·기존 로그가 있어 전체 clean은 아니었다. 작업 중 HEAD 변경은 이 증거의 단위별 commit 때문이다.
- `week-08.html:736–766` 확인. `weeks/week-08/README.md`, `scripts/check_week08.py`는 현재 checkout에 없었다. 공식 제출 요구는 HTML 764–766행을 근거로 삼았다.

## 한 번 조회한 Round 3 board

조회: **2026-11-12 16:23:22 UTC = 2026-11-13 01:23:22 KST**, HTTP 200, `round=3`, `current=3`.
서버 응답에 별도 timestamp 필드가 없어 관측 VM UTC를 기록했다. 계정 프로필·타인 행·카드·후기·원문 응답은 내보내지 않았다.

| 지표 | 관측값 | 분모·정의 |
|---|---:|---|
| 내 shop 정산 `deals` | 17,732 | board 누적, 부분 로그 아님 |
| 내 buyer 정산 `deals` | 17,954 | board 누적, buyer orders 27,840 |
| shop / buyer alive | true / true | 해당 조회 시점 상태 |
| shop ALL / buyer ALL | 0.775 / 0.657 | 공개 board 원래 값 |
| Players total | 1.432 | 위 ALL 두 값의 합 |
| student_shop / student_buyer | 0.724 / 0.599 | 학생 집단용 값, 위 ALL과 구분 |
| student_total | 1.323 | 학생 집단 순위에 사용 |
| 학생 Players 순위 | **17 / 46** | `student=true`, student_total 내림차순 경쟁순위; 동점 자기 포함 1명 |
| student_pct | **0.644 = 64.4%** | 서버 제공 값; 최종 종료 백분위 아님 |

현재 관측상 shop·buyer 각각 20건 조건보다 많다. **종료 시각 최종 순위나 제출 완료를 증명하지는 않는다.**

## 부분 로그의 정확한 커버리지

실제 VM의 `market-kit.log.1`, `market-kit.log`를 **각 끝 4,194,304바이트만** 읽고 VM 메모리에서 집계했다. 전체 다운로드 없음. 시작·끝의 불완전한 줄은 버렸으며 각 읽기 중 크기·mtime은 안정적이었다.

| 파일 | 전체 크기(읽기 시작) | 읽은 complete 줄 | 시계 범위(날짜 없음) | seller settled | buyer paid | checkout refused |
|---|---:|---:|---|---:|---:|---:|
| `.log.1` | 20,166,378 B | 1,585 | 15:32:00–15:45:46 | 3 | 4 | 1 |
| `.log` | 10,504,288 B | 1,501 | 16:08:30–16:23:21 | 0 | 2 | 1 |

- 합계 8,388,608바이트 / 3,086 complete 줄. **board 누적 정산과 다른 관측 단위·분모**다. 두 tail 사이에도 미관측 구간이 있다.
- 이 범위의 settle-refused 0줄, payment-not-completed 0줄, payment-failed 0줄. 전체 Round 3 오류가 없다는 뜻이 아니다.
- 비배타적 문구 포함 줄: `out of stock` 51, `timeout` 43, `declined-price-policy` 31, `nonpositive expected Saved` 31, `not paying:` 1. 앞 두 문구는 넓은 탐지 기준이며 인증된 시장 정산 거절 코드나 고유 실패 주문 수가 아니다. 두 price-policy 문구는 같은 줄에서 겹칠 수 있다.
- seller 정산과 buyer 결제가 같은 거래일 수 있어 3+6을 고유 거래 9건이라고 해석하지 않는다. 공개 정산 seq 범위와 파일별 unique count는 JSON에 별도 기록했다.
- 모델 경고 문구 0줄은 모델 성공·미사용의 실측 증명이 아니다. 모델 경로는 아래 구조 근거로만 구분한다.

### 안전한 실제 인용 5개

아래는 같은 원문 줄에서 추출한 **서로 떨어진 정확한 부분 문자열**이다. `…`는 생략 표시이며 완전한 원문 줄·원본 로그라고 부르지 않는다. 고객·주문·unit 식별자, URL, 서명, 예산은 내보내지 않았다.

1. `.log.1` 15:41:25: `[acp] paid` … `ledger #5703000`
2. `.log.1` 15:43:24: `[acp] settled` … `ledger #5703603`
3. `.log.1` 15:43:28: `[acp] paid` … `ledger #5703628`
4. `.log` 16:17:50: `[acp] paid` … `ledger #5712910`
5. `.log` 16:23:08: `[acp] paid` … `ledger #5714330`

별도 파일: `logs/settlement-substrings-excerpt.txt`. **제출 필수 `logs/market-kit.log` 대체물이 아니다.**

## 운영 구조에서 반드시 바로잡을 사실

- 운영 seller 협상 예약: **created_at부터 >90초**, idle 기준 아님. signed-receipt 후 committed 예약: **>120초**. 둘 다 다음 execute 시 정리된다. ACP checkout 세션 자체의 TTL과 혼동 금지.
- 운영 `seller_cash_policy.py`는 **단일 파일 588행**. 로컬 `_cash_*` 4개 모듈은 VM에 없다.
- 운영 seller는 협상·가격·선택을 규칙으로 실행한다. Round 3 market_buyer도 LLM 호출 branch를 우회한다. forum writer는 `FORUM_USE_MODEL=False`이다.
- 잔존 legacy 경로에 `gpt-5.6-sol`, Gemini 후보 문자열이 있지만 실제 Round 3 성공 모델 호출로 보고할 수 없다. 현재 증거 작성 세션 모델 GPT-6.1 Sol과 운영 모델을 구분한다. 과거 구현 담당의 모델은 이 조사에서 입증하지 않았다.
- 운영의 실제 노드·연결·메모리 보존 규칙 및 행 번호는 `runtime-structure.md`, 로컬 미배포 차이는 `pending-differences.md` 참조.

## 제출 로그 원본 상태 — 남은 명시 작업

기존 로컬 `submissions/25512085/week-08/logs/market-kit.log`는 **4,318,210바이트, mtime 2026-11-05 10:28:40 UTC**이다. `.1`도 11월 5일 파일이다. 이번 VM 현재 로그와 크기·시점이 다르므로 **최신 VM 원본과 동일하지 않다**. 예전 VM에서 온 파일인지의 원산지는 이번 자료만으로 확정할 수 없다.

기존 두 로컬 로그의 SHA-256을 전후 검증하여 보존했다. 전체 VM 로그는 읽지 않았으므로 전체 remote SHA-256은 만들지 않았다. 제출용 원본 로그 수집·업데이트는 **별도 명시 승인과 민감정보 처리 판단이 필요한 후속 작업**이며, 이번에는 수행하지 않았다. 기존 미추적 로그도 stage하지 않았다.

## 재현과 한계

- `runtime-manifest.json`: 소스 SHA-256·VM HEAD·안전한 agent 명령·역할별 process 시작시각·초기 stat.
- `runtime-final-readonly-verification.json`: VM 소스와 HEAD 최종 재대조, global tracked dirty 수.
- `sanitizedmetrics.json`: board 1회 + tail별 집계와 정확한 byte offset/hash.
- `source-comparison.json`: 원시 SHA와 줄바꿈 정규화 비교를 분리. **9개 동일, 6개 내용 차이, VM에 없는 로컬 파일 7개**. 양쪽 없는 `scripts/a2a.py`도 명시.
- `verification.json`: 로컬 원본 보존, 17개 Python 파일 AST 구문 확인, 집계 불변식. 애플리케이션 테스트·live 결제·LLM 호출·대규모 조회는 실행하지 않았다.
- 실행 명령과 독립 검증은 `README.md`. 지표의 인과효과·개선율·전체 실패율·통계적 유의성을 추론하지 않았다. 소스 해시는 디스크/HEAD 증거이며 실행 중 프로세스 메모리 덤프나 모듈 bytecode 해시가 아니다.
