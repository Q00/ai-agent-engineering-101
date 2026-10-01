# 제출용 집계의 정의와 재검증

최신 실험 `20260922T012131-no-token-limit-9703d2`의 원본을 과제의 8열 CSV로 변환했다. 모델을 새로 호출하거나 실험 로그를 수정하지 않는다.

## 파일과 실행 단위

|파일|범위|
|---|---|
|[../results.csv](../results.csv)|최초 45회 + 429 복구 16회 = 61개 실제 시도, 과제 지정 8열|
|[RESULTS_BY_RUN.md](RESULTS_BY_RUN.md)|CSV 61행을 전부 표시한 표와 실행별 콘솔·전체 협의 기록 링크|
|[BLOCK_RESULTS.md](BLOCK_RESULTS.md)|조건×원래 회차로 묶은 9행, 보고서와 동일|
|[results-primary.csv](results-primary.csv)|최초 45회만 분리한 보존용 내보내기|
|[results-recovery.csv](results-recovery.csv)|본 실험에서 실패한 16개 슬롯의 별도 복구|
|[evidence.json](evidence.json)|각 메시지의 원본 줄 번호, 파일 해시, 행별 부분 관측|
|[../results-allocation-reference.csv](../results-allocation-reference.csv)|이전 기본 배정 CSV 14행의 원본 보존본|

실제 실행기는 작업 하나마다 별도 프로세스와 `run_id`를 생성했다. 따라서 제출 CSV는 **실제 실행 1개 = 1행**이고 완료 행의 `tasks=1`이다. 각 조건·회차는 사전에 정한 동일한 5개 작업을 포함한다. 최초 45개 슬롯에서 16개가 429로 중단돼 재실행했으며, 완료 행 45개는 원래 슬롯에 중복 없이 하나씩 대응한다. 보고서의 9개 묶음은 이 행들의 집계이지, 존재하지 않는 실행 ID를 새로 만든 것이 아니다. 하위 재귀 작업 수를 `tasks`나 gold의 분모에 넣지 않는다.

## 필드 정의

- `correct`, `misawards`: 최초 책임자만 사전 `tasks.json`의 gold와 비교한다. 하위 작업에는 사후 gold를 붙이지 않는다.
- `messages`: 모든 깊이에서 **propose의 `call_start` 1건 = 공고 1건**, 로컬 검증을 통과한 `proposal.bid=true` 1건 = 입찰 1건, `award` 1건 = 낙찰 1건으로 센다. 리뷰·실행·통합·거절·잘못된 제안·HTTP 재시도는 이 세 메시지 유형이 아니다. 전체 API 호출 수는 `note.calls`로 별도 기록한다.
- `unassigned`: 완료한 실행에서 담당자가 없는 루트의 수다. 이 실험에서 복구를 포함해 완료한 45개 실행에는 모두 루트 낙찰자가 있으므로 0이다. HTTP 장애 때문에 낙찰에 도달하지 못한 실행을 정상적인 무응찰로 바꾸지 않는다.
- `note`: 작업, 회차, 상태, gold, 최초 책임자, facts 통과, 메시지 구성, API 호출·429 수, 원본 콘솔과 전체 JSONL 경로를 기록한다.

본 실험의 HTTP 429 중단 16행은 과제 규약대로 `tasks,correct,messages,unassigned,misawards`를 **모두 공란**으로 남기고 원래 오류를 `note.error`에 보존한다. 공란은 0이 아니다. 15행은 루트 배정 전, 1행은 C에게 결제 작업을 배정한 뒤 하위 작업에서 실패했다. 후자의 부분 오배정도 `note.observed_partial`에 남기지만 완료행 합계에는 더하지 않는다. 여기의 `root_unawarded`는 단순히 낙찰 기록이 없다는 뜻이며 정상적인 무응찰 지표와 구분한다.

보고서 표는 **429 복구를 포함한 최종 45개 슬롯**을 평가한다. 완료 행의 gold 합계는 **17 정답·28 오배정**, 필수 facts 통과는 **44/45**다. 최초 시도만의 성공률이 아니다. 실패 전 부분 배정은 다시 더하지 않으며, 메시지 합계도 완료 시도에 한정한다. 중단된 시도에서 소비한 메시지·API 호출 수는 note에 남아 있다.

복구 16행이 원래 실패한 슬롯과 정확히 일치하고, 최종 완료 슬롯이 45개로 유일한지 검사한다. 이 합산 방식은 실험 후 사용자 요청에 따라 정했으며, 최초 45회와 복구 16회는 별도 CSV로도 보존한다. 복구 중 facts 오답 1건도 실행 자체는 완료했으므로 수치를 숨기지 않는다. 산출물 생성 성공, gold 일치, facts 통과는 서로 다른 지표다.

## 원본 보존과 재현

45회 본 실험과 16회 복구의 기존 `.console.log` 61개와 전체 협의 `.jsonl` 61개를 루트 `logs/`에 **바이트 단위로 동일하게 복사**했다. 콘솔에는 실행 요약이 있고, 공고·입찰 이유·확신도·낙찰은 JSONL에서 확인한다. [기록 읽기](../logs/README.md). 기존 JSONL·콘솔·산출물은 그대로다. 과거 배정 CSV의 보존본 SHA-256은 `dc215d0e6db38ec27245e3a2f058d83c01d1526d4b70aa5812ba13d76c462bf1`이다.

```bash
# 표준 라이브러리만 사용. 원본 로그에서 CSV·보고서 표·해시를 재계산해 대조한다.
python3 submissions/26622007/week-03/submission_results.py --check

# 같은 원본에서 파생 파일을 다시 생성한다. 기존 로그가 다르면 덮어쓰지 않고 실패한다.
python3 submissions/26622007/week-03/submission_results.py --write

# 루트 배정/하위 작업, 실패 뒤 부분 배정, 거절/재시도, 복구 포함·중복 방지의 회귀 검사
python3 -m unittest discover -s submissions/26622007/week-03 -p 'test_submission_results.py' -v
```

현재 런타임 코드와 본 실험의 소스 커밋 `d3014d2b3fbda00c262d5fd5f56402e03a4019e0`의 관계, 실제 병렬 중첩·429·산출물 품질의 상세 관측은 [이전 상세 작성본](../REPORT_DETAIL_20260922.md)과 [실험 원본 보고서](../extensions/peer_dag/conditions/20260922T012131-no-token-limit-9703d2/FINAL_REPORT.md)에 있다. 과거 상세 작성본의 작성란은 현재 [제출 보고서](../REPORT.md)에서 대체·완성했다.
