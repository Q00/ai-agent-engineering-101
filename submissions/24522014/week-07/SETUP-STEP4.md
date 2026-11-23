# SETUP Step 4: 시장에 들어가기

아래는 최초 참가 당시 기록이다. 이후 LAB 1에서 바뀐 실행 PID와 가게 주소는 `LAB1-STEPS345.md`를 참조한다.

2026-10-20, 학번 24522014 (GitHub: kcwarni).

공식 `scripts/market_kit.py join`으로 https://market.ourolabs.ai 에 가게와 buyer를 등록했다. 닉네임은 `kcwarni`, 연결된 GitHub 계정도 `kcwarni`다. 현재 시장은 Round 0이다.

- 가게: https://immune-benchmark-mixed-subaru.trycloudflare.com
- 공개 agent card 응답: HTTP 200.
- 시장 board에서 shop과 buyer 모두 `alive: true`, owner login `kcwarni` 확인.
- 첫 구매: Corner Store의 lamp, 예산 140, 가격 130, 원장 #851, `verified True`.
- 등록 검증 시 buyer의 Round 0 거래 수: 2.

## 실행 환경과 실패 기록

`run_market.py`는 학생 디렉터리의 환경을 준비한 후 공식 키트를 그대로 실행한다. 최초 httpx 접속은 인증서 체인 검증에 실패했다. Windows 신뢰 인증서와 certifi 인증서를 합친 로컬 번들을 `tools/windows-ca-bundle.pem`에 만들고 `SSL_CERT_FILE`로 지정했다. TLS 검증은 계속 사용한다.

최초 터널 실행은 등록에 성공했으나 cloudflared 출력 처리 스레드가 cp949 디코딩에 실패했다. 해당 실행을 종료하고 `-X utf8`과 `PYTHONUTF8=1`로 재시작했다. GitHub claim code는 환경변수 `MARKET_OWNER`로 전달했으며 저장소에 저장하지 않았다. 키트가 생성한 `shop_key.pem`, `buyer_key.pem`, 로컬 인증서 번들, 실행 PID 파일은 Git에서 제외한다.

최초 board 검증은 URL이 최상위 필드에 있다고 가정해 실패했다. 실제 `card.url`로 수정한 재검증은 통과했다. 실패 로그와 성공 로그를 모두 보존한다.

## 재실행 및 중지

키트는 숨긴 백그라운드 프로세스로 실행 중이다. 시작 당시 PID는 7284이며 로컬 상태는 `.market/process.json`에 있다. 같은 키트를 중복 실행하지 않는다. 이 실행의 프로세스 트리를 종료하려면 `taskkill /PID 7284 /T /F`를 사용한다. PID는 이후 재실행 시 달라진다.

저장소 루트에서 수동으로 다시 실행할 때:

```powershell
$env:MARKET_OWNER = Read-Host 'Market claim code'
& ./submissions/24522014/week-07/.venv/Scripts/python.exe -X utf8 ./submissions/24522014/week-07/run_market.py --tunnel
Remove-Item Env:MARKET_OWNER
```

직접 실행한 터미널에서는 Ctrl+C로 종료한다. 재시작하면 터널 URL이 바뀐다. 키 파일은 유지해야 같은 참가자로 이어진다. 컴퓨터가 꺼지거나 절전 상태에 들어가면 가게와 buyer 실행도 유지되지 않는다.

## 증거

- `logs/market-kit.log`: 공식 키트의 등록·주문·원장 기록. 실행 중 계속 추가된다.
- `logs/setup-step4-*.stdout.log`, `*.stderr.log`: 각 실행 원본 출력.
- `logs/setup-step4-tls-failure.log`: 최초 TLS 오류 재현.
- `logs/setup-step4-verification.log`: 최초 board 검증 실패.
- `logs/setup-step4-verification-retry.log`, `logs/setup-step4-registration.json`: GitHub 연결, 가동 상태와 공개 카드 응답 확인.

Step 4 시장 참가까지 완료했다. 현재 코드는 week-06 buyer를 사용한다. LAB 1의 market_buyer와 LAB 2의 seller 서명, Round 2 과제 요건 충족은 이후 작업이다.
