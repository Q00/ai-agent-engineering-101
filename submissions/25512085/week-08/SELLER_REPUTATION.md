# 평판 독립 관측 foundation

`seller_reputation.py`는 명시적으로 수동 실행하는 관측 도구다. 자동 정책이나
평판 개선 에이전트가 아니다. seller/review/restock/monitor, `agent.json`,
기존 hub/filelock과 연결하지 않았고 운영 원본·키·로그를 읽지 않는다.
학습과제의 핵심 의사결정은 사용자가 결과를 이해하고 직접 수행한다.

## 데이터 계약과 한계

- 입력은 **한 번의 응답 배열**뿐이다. 이전 상태를 받거나 리뷰를 병합하지 않는다.
  전달받은 공식 GET 관측 정보에 따르면 응답에 안정적인 리뷰/거래 ID가 없으며
  전체 이력·페이지네이션 보장도 없다. 같은 내용도 삭제하지 않고
  `possible_duplicate_records`만 센다. 이는 동일 dict의 재등장 진단이지 중복 확정이 아니다.
- `owner.login`을 trim/casefold하여 계정을 구분한다. `author`는 에이전트이며
  계정 대체값으로 사용하지 않는다. owner 미상은 별도 개수로 남기고
  가상의 pooled account를 만들지 않는다.
- `raw_review_mean`: 유효 별점 전체 평균(계정 미상 포함).
  `observed_account_mean`: 관측된 계정 각각의 평균을 동등 가중한 평균(계정 미상 제외).
  예: A의 `[5,5,5]`, B의 `[1]`이면 각각 **4**, **3**이다.
  두 값의 차이는 가중 방식/계정 식별성 차이이며 플랫폼 오류 증거가 아니다.
  두 값 모두 전체 이력 평점이나 공식 `stars_rank`를 재현하지 않는다.
- 계정별 `n/mean/low_count/last_ts`는 유효 별점 리뷰만 기준이다.
  별점은 bool·비유한값·1~5 범위 밖·문자열을 제외한다.
  계정 미상 records와 유효 별점 미상 개수를 구분한다.
  invalid record/stars/time/text 계수는 서로 겹칠 수 있다.
- 시간은 epoch seconds 또는 timezone이 있는 ISO 형식만 받는다.
  잘못된 시간은 별점 자체를 버리지 않는다. 응답 time range는 모든 유효 시간,
  계정 last_ts는 해당 계정의 유효 별점+유효 시간 기준이다.
- 요청 shop/round는 출처 메타데이터다. 리뷰에 해당 필드가 명시되면 불일치를
  거부한다. 없으면 `requested endpoint, not per-record verified`라고 표시한다.
  offline envelope에는 요청 shop/round/fetched_at가 반드시 있어야 한다.
- `whole_history=false`, `not_official_rank=true`, response_count/time_range,
  fetched_at/analyzed_at/age_seconds/freshness/history_gap을 함께 출력한다.
  freshness는 수집시각의 나이일 뿐 리뷰 이력의 완전성이나 최신 리뷰 보장이 아니다.
  순수 함수에는 fetched_at와 now를 주입하므로 재현 가능한 테스트가 가능하다.

## 저평점 진단은 원인 확정이 아니다

기본값은 `unknown`이다. 선택적 `--cues`는 작은 영어 규칙으로
가격/품질/보증/지연 텍스트 단서만 표시하며 **unverified text cues**다.
키워드가 있어도 결함 사실·거래 연결·원인은 입증되지 않는다. 부정 표현이 있는
텍스트는 보수적으로 unknown 처리하며 일반적인 긍정/부정 문장도 unknown이다.
`Payment settled; no unobserved quality or failure claims.`의 1점 역시 unknown이다.
부정 감지·어휘는 불완전하며 한 리뷰가 여러 단서를 포함할 수 있다(태그 합계와
리뷰 수가 같다고 보장하지 않음). LLM 호출이나 자동 가격/품질/보증 조정은 없다.

사용자가 검토 후 조절할 상수는 모듈 상단의 `LOW_STARS=2`, `CUE_PATTERNS`,
`NEGATION`, `SNIPPET_CHARS=160`, `EXAMPLES_PER_TAG=3`, `STALE_SECONDS=3600`이다.
네트워크 경계는 `TIMEOUT_SECONDS=10`, `MAX_BYTES=2MiB`이다.

## CLI: 기본은 네트워크 없음

아래는 **추후 사용자가 수동 실행할 명령 예시**다. 이번 작업에서는 실제 GET을
실행하지 않았다. 디렉터리는 `submissions/25512085/week-08` 기준이다.

합성 fixture 예시(코드 블록을 별도 JSON 파일로 저장):

```json
{
  "shop": "synthetic",
  "round": 3,
  "fetched_at": "2026-11-06T00:00:00Z",
  "reviews": [
    {"owner": {"login": "AccountA"}, "author": "synthetic-agent", "stars": 5,
     "review": "Synthetic positive feedback.", "ts": "2026-11-06T00:00:00Z"}
  ]
}
```

```powershell
python -B seller_reputation.py --input synthetic.json --shop synthetic --round 3
# 실제 조회를 원하는 경우에만 실행: 공개 고정 HTTPS GET 한 번
python -B seller_reputation.py --fetch --shop mjforge --round 3
# 저장은 명시적으로만: 부모 폴더를 미리 만들고 매번 새 파일명을 선택
python -B seller_reputation.py --fetch --shop mjforge --round 3 --output reputation_snapshots/manual-unique.json
```

`--input`/`--fetch` 중 하나를 반드시 선택한다. offline 결과에는 실제 GET으로
독립 검증되지 않았다는 mode가 붙는다. GET은 고정 trusted HTTPS endpoint에만
query encoding을 적용하며 리다이렉트는 같은 origin도 전부 거부한다.
응답 크기 제한·timeout이 있고 재시도/폴링/board 추가 조회는 없다.

기본 출력은 stdout이다. 개인정보가 포함될 수 있으므로 stdout도 무심코 로그나
커밋에 남기지 않는다. 계정/스니펫이 포함된 실제 결과와 raw review는 커밋하지 않는다.
저장 권장 폴더 `reputation_snapshots/`는 gitignore 처리했다.
`--output`은 JSON 직렬화를 먼저 한 뒤 `open('x')` 배타적 생성으로 저장하며
기존 파일(동시 실행 포함)은 거부한다. 원자적 전체 파일 교체는 아니다:
디스크 오류/중단 시 새 파일이 불완전할 수 있으므로 JSON 유효성을 확인하고
실패 재시도에는 새 경로를 사용한다. 부모 폴더는 자동 생성하지 않는다.

## 검증 및 수동 운영 계획

```powershell
python -B verify_cash_refactor_offline.py baseline
python -B verify_cash_refactor_offline.py after
```

러너는 외부 연결/DNS/프로세스 실행과 운영 원본 읽기, 저장소 내 쓰기를 막고
고유 임시 fixture와 배타적 신규 테스트 로그만 사용한다. 신규 테스트는 전부
합성이며 fetch/timeout/대용량/리다이렉트는 mock으로 검증했다.
기준: 307개 통과. 최초 추가 후: 323개 통과. 극단 숫자와 수동 출력 경계를 보강한
최종 결과: **326개 통과(신규 19개)**. 실패 테스트는 없었다.
각 실행의 명령·결과는 신규 `logs/cash-refactor-{baseline,after}-20261106-*.log`에
있다. 기존 dirty 로그·test-report·evidence는 수정/정리하지 않았다.

주차 README(`weeks/week-08/README.md`)는 저장소에 없어 전달된 범위 명세를 따랐다.
실제 GET, API 인증 실행, VM 배포, 거래, 설치, push는 수행하지 않았다.
통합 CI 주차 검사도 해당 스크립트/명세 부재로 실행하지 않았다.

다음 운영 단계는 사용자가 명시적으로 수동 조회 → coverage/quality 확인 →
계정별·raw 평균 차이와 unknown 원인을 검토하는 것이다. 자동 연결/배포나
리뷰 조작·자동 답변·자동 재고/가격 정책 변경은 별도 승인 및 설계 없이 하지 않는다.
