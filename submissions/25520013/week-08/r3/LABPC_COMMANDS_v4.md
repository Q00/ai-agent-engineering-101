# labpc 명령 v4 (v1.17d fixes4) — 준비만 함, 실행하지 않음. 실행은 EL이 Mac에서, 승인 후.

v3 대비: 182738 폐기(원장 close released 10, 미전송·호출 0). 원장 HWM(ledger_hwm/) 부착 — 원장 삭제·잘림·이동 시 모든 명령 거부.
드라이버는 `labpc/realrun_driver_v4.py`. 주입 문구는 존재하지 않는 카나리 경로(`~/.r3_canary_absent_<nonce8>`)만 씀.

PY=/workspace/course/r3/.venv/bin/python ; D4=/workspace/course/r3_v117_llm/labpc/realrun_driver_v4.py

## 대상 (전역 상한 20 = 재측정 10 + 제안자 10, 두 회차 모두 예약됨, 미전송)
| 회차 | scope | n | nonce | batch.sh sha256 | 본문(payload) sha256 | SHA256SUMS sha256 | 최악 |
|---|---|---|---|---|---|---|---|
| realrun_20261103T184947 | remeasure | 10 (editor 3, parse 4, short 1, injected 2) | d9ea999d1c9246f7b537638809bdc48f | 1056a46b0b9a6363ec0b5117c170dbf01b6df065d6878c1c68585d1b203dda94 | 6f9de02a38b62865ef7ba43cb70c617156ea9ffe53276ca02c07212ab9105ca0 | f99b7856a42c81c58906057bc426fc03b30176d0724c7e6b5ffc6c8240cb9ad5 | 442 s |
| proposer_realrun_20261103T184947 | proposer | 10 (memo 8, injected 2) | 7a769871e4ed4135bfd9cdd9692b909d | 5df32c1515dd6293986c3cf1a9143a2b13e2fcbb3327cabd2808fc78eb207545 | 9772c820e6418ef05ac6d0eb48eaa73fd53250e37c739642812fb24259783737 | 116b2cfe7af0674fbd82495ab1b399136770ea096418f7f26a08f266a1f34a63 | 350 s |

제안자 회차 승인 근거: "CoS 중계 18:40 (성수님)" — EL은 원문 미확인. approvals 행이 추가되기 전에는 보내지 않음.

## 순서 (회차마다 따로, 한 번에 한 회차)
1. box: `cd <dir> && sha256sum -c SHA256SUMS` — 전부 OK. SHA256SUMS 자체 sha가 위 표와 같아야 함.
2. box: `$PY $D4 ledger` — committed 20, 두 회차 open, ledger_sha256이 approvals에 적힌 값과 같아야 함.
3. box: `$PY $D4 mark-sent <dir>` — 이후 abandon 불가.
4. Mac: **Shell 호출 1회**로 `ssh labpc 'wsl bash -l -s' <<'R3EOF'` + batch.sh 내용 그대로 + `R3EOF`.
   양자내성 경고 줄만 걸러냄. 백그라운드 가능, 최악 시간은 위 표. **재시도 없음** (자동·수동 모두).
5. box: 표준출력 전체를 `<dir>/raw_stdout.txt`로 저장하고 곧바로 `$PY $D4 capture <dir>` (raw sha를 원장에 1회 기록).
6. box: `$PY $D4 ingest <dir>` → calls.jsonl, summary.json, SHA256SUMS_ingest, REPORT_5_4_<회차>.md
   (START host, CODEX_VERSION, stdin_sha/expected_sha 일치, eof=1, SHA256SUMS_ingest sha 포함).
   비밀 형태 문자열이 있으면 원본은 quarantine/(0600)로 옮겨지고 raw_stdout.redacted.txt만 남음.

## 판정
- START의 nonce가 원장 예약 nonce와 같고, stdin_sha == expected_sha, eof=1 이어야 함.
- 호출 0회로 인정되는 경우는 둘뿐: (A) START에서 sha 불일치/eof=0 + INTEGRITY_FAIL 한 줄, (B) 무결성 OK 뒤 PREFLIGHT_FAIL 한 줄
  (PREFLIGHT_OK·BEGIN·CALL·END 없음). 그 밖의 모양은 전부 n회 사용.
- 결과가 불확실하면(시간 초과, 연결 끊김, START 없음, raw 일부만): 다시 보내지 않음.
  `$PY $D4 close-uncertain <dir> --reason "..."` → n회 전부 사용으로 닫음.
- 끊긴 뒤 labpc 확인은 읽기 전용 `pgrep -af 'codex exec'`만. kill은 별도 승인. `timeout -k`가 t+5 s 안에 끝냄.
- 보내지 않기로 하면(mark-sent 전에만): `$PY $D4 abandon <dir> --reason "..."` (예약 반환).

유지 조건: 거래·kit·tmux·policy.json 무관, labpc/Mac에 파일 쓰기 없음, 비밀값 읽기·출력 없음, stderr 버림, 배포 OFF,
caps_v4는 배포에 연결하지 않음.
