# Shop 오프라인 복구 — 2026-11-10 12:52 KST

최종 확인에서 Shop·Buyer 모두 온라인, 카드·ACP 응답 200, 제어 상태 `observe_and_trade`다. 새 주소는 `https://antonio-bicycle-investors-chuck.trycloudflare.com`이다. 계정·키·시장 자산·거래 원장은 유지했다.

## 관측과 조치

- 최초 진단: 기존 주소의 카드·ACP는 정상 응답했지만 시장 활성 목록에서 빠지고 보드가 offline이었다. 다른 일부 가게도 카드 응답과 시장 표시가 달랐다. 원격 검증 실패의 시장 내부 원인은 확정하지 않았다.
- 같은 주소 재등록은 성공 응답을 받았으나 활성 목록과 보드 상태가 반복해서 달라졌다. 등록 응답만으로 복구를 선언하지 않았다.
- Buyer가 다음 협상·결제에 들어가기 전 대기하는 안전 시점에서 기존 런타임·watcher를 협력 종료했다. 새 터널과 런타임을 시작했고 동일 소유자·키로 양쪽을 재등록했다.
- Shop 감독 코드에서 등록 승인만 받으면 `market_visibility=present`, `healthy`로 판단하던 부분을 교정했다. 승인 후에는 `registration_unverified`로 두고 독립적인 활성 목록·보드 조회로 확인한다. 보드 offline 관측은 두 번째 재등록 판단 이전에도 정상 상태로 표시하지 않는다.
- 최초 구문 검증 오류는 실행 전 수정했고 실패 로그를 보존했다. 최종 관련 테스트 30개 통과.

## 최종 검증

03:51:56, 03:52:12, 03:52:34 UTC(12:51–12:52 KST)에 카드·ACP 200, 활성 목록 등록, 새 주소와 보드 주소 일치, Shop·Buyer alive=true를 연속 확인했다. 검증 구간 Shop 공개 거래 수는 7,615→7,617로 늘었다.

12:52:41 KST 최종 검사 `verified=true`. Shop 거래 7,617, Buyer 거래 10,447, 제어 상태 `observe_and_trade`. 짧은 연속 관측은 앞으로 시장 장애가 재발하지 않는다는 보장이 아니다. 감독 프로세스는 계속 실행 중이다.

## 근거

- [최초 진단](logs/market-connectivity-20261110T024222845703Z.json), [같은 주소 재등록 결과](logs/shop-registration-restored-20261110T024237519808Z.json).
- [기존 주소 상태 변동](logs/shop-recovery-watch-20261110T034214514754Z.jsonl).
- [안전한 Buyer 중단 기록](logs/buyer-v12-drain-20261110T034627545627Z.json).
- [새 주소 연속 검증](logs/shop-recovery-watch-20261110T035155233140Z.jsonl), [최종 전체 검증](logs/autonomous-verified-20261110T035241532261Z.json).
- [테스트 30개 통과](logs/shop-recovery-verification-tests-final-20261110-1248.txt), [감독 코드](r3/transport.py), [회귀 테스트](tests/test_transport.py).
