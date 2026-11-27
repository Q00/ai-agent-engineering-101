# Round 3 숫자-only interop — 로컬, 미배포

## 범위

- 작업 경로와 AGENTS.md/POLICY.md/TURN_TELEMETRY.md 확인. 기존 dirty 로그·test-report와 다른 untracked 자료는 수정·stage하지 않았다.
- 실행 코드 변경은 market_buyer.py 두 표현식뿐: 일반 Round 3 `str(offer)`, 최종 수락 `str(seller_ask)`.
- 최초 SKU/인증/보증 문의, legacy 문장, 35% gap/가격 상한/제안 횟수/cooldown, 서명·영수증·taskId와 계측 코드는 불변.
- 기존 price/retry/telemetry fake seller의 wire 파싱/문구 기대만 갱신. 정책 수치와 기존 assertions는 유지.
- 새 strict seller는 `re.fullmatch(r'([0-9]+)', text)`만 수락. 네 제안 `[60000,74000,93104,80000]`, 마지막 호가 수락의 합성 receipt, taskId, 13개 계측/미결제 표시, 첫 호가 즉시 수락, 최초 식품/보증 문의와 legacy 문구를 확인.

## 실제 검증 명령과 결과

모두 저장소 루트에서 `python -B submissions/25512085/week-08/history/20261106-integer-wire-01/check.py <label>` 사용. 4회 실행:

| label | tests | failures/errors | 결과 |
|---|---:|---:|---|
| baseline | 495 | 0/0 | 기존 코드 기준선 통과 |
| red | 498 | 3/0 | 기존 문장형 전송을 strict seller가 거부, 예상 실패 |
| after-01 | 499 | 1/0 | 새 legacy fixture의 item 인자를 잘못 전달한 실패 보존 |
| after-02 | 499 | 0/0 | fixture 인자 수정 후 전체 통과 |

`git diff --check`도 통과(Windows LF/CRLF 경고만 있음). 각 실행의 stdout/stderr와 소스 SHA256은 이 디렉터리의 새 label 파일로 보존하며 덮어쓰지 않았다.

check.py는 tracked 직속 Python 소스와 이번 신규 테스트, 정책 JSON, launcher, kit 사본만 새 evidence 경로로 복사한다. unittest discovery로 이번 신규 테스트를 포함한다. 기존 untracked test_market_monitor_offline.py는 사용자 작업으로 간주해 포함하지 않았다. 기존 안전 runner를 이 폴더에 복사하여 원본 저장소 읽기/외부 socket/격리 밖 쓰기를 차단하고 HOME·TEMP도 새 경로를 사용했다. 기존 운영 state/market/key/env는 복사·읽지 않았다.

## 한계

- 실제 상대 파서는 미확인. 합성 strict integer seller의 task 완료/receipt 선택만 증명하며 실제 결제·Saved·체결률 개선은 증명하지 않는다.
- VM 배포/접속, 실제 RPC·거래·네트워크·모델·설치·push는 수행하지 않았다.
- 중복 재전송 방지/무진행 cooldown은 후속 별도 승인 범위로 구현하지 않았다.
- weeks/week-08/README.md와 scripts/check_week08.py는 현재 저장소에 없어 공식 주차 checker는 실행하지 못했다.
- 테스트/실패 증거 커밋 `cc62b97`, 두 전송식 수정과 첫 검증 실패 보존 `0baab04`; 이후 fixture 수정과 최종 통과 증거는 별도 커밋.
