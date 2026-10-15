# Week 06 — A2A 구매·판매 에이전트

실제 Codex CLI가 상품·가격·수락·종료를 판단하는 A2A 1.0 에이전트입니다. 구매자와 판매자는 독립적으로 협상하고, Python 코드는 통신·검색·예산·거래 증거를 검증합니다. 현재 구조는 [한국어 Mermaid](ARCHITECTURE.md), 제출 요구사항과 **정리 후 재실행 결과**는 [REPORT.md](REPORT.md)를 확인하세요.

현재 운영에 필요한 소스·스키마·카탈로그·고정 정책·회귀 검사와 제출 문서만 유지했습니다. 이전 학습 도구·후보·비교 실행기·이미지·로그는 Git 이력 `4762fd350bb5891fb02d70eb2e4d0f26d8354fc7`에 남아 있습니다. 아래 명령으로 새 로그와 공식 보고서를 생성합니다. 과거 측정값은 새 실행의 통과 근거가 아닙니다.

## 실행 준비

Linux·WSL·macOS와 Python 3.10 이상이 필요하며 3.12를 권장합니다. 아래는 **저장소 루트**에서 실행합니다. 가상환경은 제출 폴더 밖에 만듭니다.

```bash
python3 -m venv /tmp/week06-venv
source /tmp/week06-venv/bin/activate
python3 -m pip install -r submissions/26510358/week-06/requirements.txt
codex login status
```

Codex CLI가 `PATH`에 있고 개인 환경에서 로그인되어 있어야 합니다. 로그인되지 않았다면 본인 터미널에서 `codex login`을 실행하세요. 기존 인증을 사용하며 인증 파일이나 API 키를 제출하지 않습니다. 이미 준비한 `~/a2a-lab/.venv`도 사용할 수 있습니다.

기본 추론은 `gpt-6-sol`, reasoning effort `low`입니다. 빈 임시 디렉터리에서 비대화형 CLI와 역할별 JSON 스키마를 사용합니다. 호출당 최대 40초, 검증 실패 시 최대 4회 재질문하며 가격을 코드로 보정하지 않습니다. 정확한 명령 생성은 `harness.py`, 제출 실행 명령은 [agent.json](agent.json)에 있습니다.

## 먼저 한 가게와 거래하기

첫 번째 터미널에서 가상환경을 활성화한 뒤 제출 디렉터리로 이동해 판매자를 실행합니다.

```bash
cd submissions/26510358/week-06
python3 seller.py --port 9999
```

다른 터미널도 같은 가상환경·제출 디렉터리를 사용해 구매자를 실행합니다.

```bash
python3 buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120
```

Agent Card는 `http://127.0.0.1:9999/.well-known/agent-card.json`입니다. `logs/runtime/*.jsonl`에서 `quote_normalized`, `harness_decision`, `receipt_checked`, `purchase_completed`를 확인할 수 있습니다. 판매자 터미널은 거래 관찰 후 `Ctrl+C`로 종료합니다.

## 공식 검사와 필수 도전 재현

가상환경이 활성화된 **저장소 루트**에서 실행합니다. 공식 거래 검사는 `agent.json`의 명령으로 판매자·구매자를 직접 시작하고 실제 모델을 호출합니다.

```bash
python3 scripts/trade_test_week06.py submissions/26510358/week-06
python3 scripts/check_week06.py submissions/26510358/week-06
```

필수 도전과 회귀 검사는 **제출 디렉터리**에서 실행합니다.

```bash
python3 run_challenges.py --challenge all
python3 -m unittest discover -s tests -v
```

도전 1은 전체 3,003개 카탈로그에서 설명에 맞는 상품을 검색합니다. 도전 2는 실제 판매자 20개의 Agent Card를 읽고 램프·의자·책장을 총예산 600으로 구매합니다. 기본 2개 협상 스레드, 개별 협상 280초, 전체 장보기 600초입니다. `--challenge 1` 또는 `--challenge 2`로 따로 실행할 수 있습니다. 157개 회귀 검사는 실제 모델 호출 없이 제약·상태·통신 동작을 검사하며 공식 모델 거래 검사를 대신하지 않습니다.

| 결과 파일 | 확인할 내용 |
|---|---|
| `test-report.json` | 공식 거래 검사 결과와 실제 사용한 제출 명령 |
| `logs/trade-test.log` | 공식 검사 전체 진행·결과 |
| `logs/seller-under-test.log`, `logs/buyer-under-test-*.log` | 공식 검사에서 실행한 각 프로세스의 출력 |
| `logs/runtime/*.jsonl` | 기본 하네스 판단·검증·A2A 요청/응답 |
| `logs/challenge-1/<실행 ID>/summary.json` | 대규모 카탈로그의 실제 선택 상품·가격·예산 검증 |
| `logs/challenge-2/<실행 ID>/summary.json` | 20개 상점 탐색·품목별 결과·원장·총예산 검증 |

각 실행의 로그 경로는 실제 출력과 [REPORT.md](REPORT.md)를 기준으로 확인하세요. `A2A_TRACE_DIR`를 지정하면 기본 JSONL 위치를 변경할 수 있습니다. 명시한 `--log-dir`나 판매자 설정의 `log_dir`가 있으면 그 경로가 우선입니다. 도전 로그의 실행 ID는 매번 새로 생성됩니다. 공식 검사는 기본 보고서와 콘솔 파일을 덮어쓰므로 다시 실행하기 전에 기존 결과를 별도 실행 폴더에 보존하세요.

## 현재 개선 사항과 한계

- metadata·DataPart의 구조화 호가를 먼저 정규화합니다. 유일한 정상 호가가 있으면 관측용 호출을 추가하지 않고, 텍스트 관측이 필요할 때만 실제 모델을 호출해 원문과 대조합니다.
- 구매자는 저가 구매, 판매자는 고가 판매와 성사를 함께 추구합니다. 모델이 선택한 장보기 공개 태도 `negotiable/firm`만 전달하며 비공개 예산·이유는 전송하지 않습니다.
- 모든 제안 전에 원장으로 예산을 예약하고 영수증 대조 후 한 번만 확정합니다. 응답 유실·상충하는 구매 증거는 분쟁으로 유지합니다. 명확한 종료와 예약 해제 확인 시에만 한 번 재선택합니다.
- 양쪽 프로세스의 추론은 로컬 FIFO 한 슬롯과 완료 후 기본 5초 간격을 공유합니다. `A2A_INFERENCE_GAP_SECONDS`로 간격을 설정할 수 있습니다. 구매 프로세스 내부 원장은 여러 스레드가 공유하며 여러 머신의 결제 데이터베이스는 아닙니다.
- `policies/active.json`의 기존 contextual bandit은 실행 중 고정되어 전략만 선택합니다. **온라인 강화학습·자동 재학습·Codex 가중치 갱신은 없으며 학습 도구도 현재 운영 파일에 포함하지 않습니다.** 시험한 새 합성 학습 후보는 승격하지 않았습니다.

구조화 필드의 통화·수량은 credits·상품 1개로 검증하지만 텍스트에만 있는 단위는 모델 판단에 의존합니다. 높은 체결가를 추구하다 상대가 먼저 협상을 끝낼 수도 있습니다. 영수증 검사는 합의 증거의 일관성을 확인하며 실물 배송이나 판매자의 신원을 보증하지 않습니다. 자세한 경계와 파일별 책임은 [ARCHITECTURE.md](ARCHITECTURE.md)에 있습니다.

## 선택 배포: Hono

Hono는 JavaScript/TypeScript 웹 프레임워크이고, Cloudflare Workers 같은 실행 환경에 앱을 배포하는 데 사용할 수 있습니다. 현재 Python A2A SDK·로컬 Codex subprocess·파일 잠금 구현을 그대로 Workers에 올릴 수는 없습니다. 현재 코드를 유지하려면 Python 실행 환경에 판매자를 배포하고, Hono·Workers를 사용하려면 판매자의 A2A 통신·상태·모델 호출을 별도로 이식해야 합니다. 시작 절차는 [Hono 공식 Cloudflare Workers 안내](https://hono.dev/docs/getting-started/cloudflare-workers/)를 참고하세요.

현재 공개 배포 URL은 등록하지 않았습니다. 실제 배포 후 `/.well-known/agent-card.json`과 거래 동작을 확인한 URL만 `agent.json`의 `deployed`에 추가합니다. 코드 작성과 점검에 Codex 개발 보조를 사용했습니다.
