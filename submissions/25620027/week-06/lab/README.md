# Week 06 — 교재 A2A CLI 실습

2026-10-13 사용자 요청: “지시대로 수행해줘. 권한줄게.” 수업 중 교재의 설치와 단계별 실습을 실제로 수행했다. 기존 24회 과제 데이터는 유지했다.

## 설치와 환경

- 공식 CLI: `a2a 0.3.0`, executable `/opt/homebrew/bin/a2a`; 교재의 참조 버전과 같음.
- Python: 3.12.11, a2a-sdk1.2.2, uvicorn0.54.0. [seller_agent.py.lock](seller_agent.py.lock)에 전체 의존성 잠금.
- 기본 시스템 Python3.9는 바꾸지 않았다. 실습 서버는 `uv run --frozen seller_agent.py`가 지정한 Python3.12 환경으로 실행한다.
- 모델·API 키 없이 실행했다. 이 CLI 실습의 모델 호출은0회다.
- 설치 명령: `brew tap a2aproject/a2a-cli https://github.com/a2aproject/a2a-cli` → `brew install a2a`.
- Homebrew6가 처음 설치를 거부해 공식 formula의 다운로드 URL·SHA를 확인한 뒤 `brew trust --formula a2aproject/a2a-cli/a2a`로 해당 패키지만 신뢰하고 설치했다. 초기 거부 메시지도 [설치 로그](logs/install-cli.txt)에 남겼다.
- 현재 검증 직후 수업용 서버를 http://127.0.0.1:9999 에 유지했다. 서버가 꺼졌으면 아래 실행 명령으로 다시 켠다.

```bash
# 이 lab 폴더에서 터미널 A로 실행
uv run --frozen seller_agent.py

# 다른 터미널 B에서 확인
a2a version
a2a card get http://127.0.0.1:9999
```

## 교재 순서대로 확인한 결과

실제 공식 CLI와 curl 명령15개를 실행했다. [완료된 실습 원문 로그](logs/lab-cli-verified.txt)에 명령·출력·종료코드를 보존했다.

| 교재 단계 | 실제 관찰 | 상태 |
|---|---|---|
| 1. SDK와 공식 CLI 설치 | Python3.12.11 / SDK1.2.2 / CLI0.3.0 | 완료 |
| 2. server 작성 | Lamp Shop, 자연어 상품 요청, 낮은 제안3회 거절, 포트9999 | 완료 |
| 3. 카드 받기 | JSONRPC http://127.0.0.1:9999/, negotiate skill | 완료 |
| 4. 버전 헤더 생략 | curl 응답 -32009, VersionNotSupportedError | 완료 |
| 5. 램프 구매 | asking130 → offer80 → counter105 → offer105 → COMPLETED와 receipt105 | 완료 |
| 5. 작업 조회 | 같은 task 조회, History 메시지5개 | 완료 |
| 6. 끝난 작업에110 제안 | UNSUPPORTED_OPERATION, CLI exit1 | 완료 |
| 7. sofa 요청 | REJECTED | 완료 |
| 7. chair에100 세 번 제안 | INPUT_REQUIRED 두 번 뒤 REJECTED | 완료 |
| 7. bookshelf 취소 | CancelTask로 CANCELED; 다시 GetTask에도 CANCELED | 완료 |

## 직접 따라 하는 명령

첫 구매 요청의 출력에서 Task 번호를 복사한다. 아래 `AX_LAB_TASK` 값은 그 번호로 바꾼다. 각 단계의 Status와 History를 읽으며 진행한다.

```bash
a2a send -a http://127.0.0.1:9999 "I want to buy a lamp"
AX_LAB_TASK="첫 응답에 나온 Task 번호"
a2a send -a http://127.0.0.1:9999 --task-id "$AX_LAB_TASK" 80
a2a send -a http://127.0.0.1:9999 --task-id "$AX_LAB_TASK" 105
a2a task get -a http://127.0.0.1:9999 "$AX_LAB_TASK"
a2a send -a http://127.0.0.1:9999 --task-id "$AX_LAB_TASK" 110 -o json
```

마지막 명령의 exit1은 교재가 의도한 종료 작업 재요청 오류다. 명령마다 `-o json`을 붙이면 규격 필드를 직접 확인할 수 있다.

```bash
a2a send -a http://127.0.0.1:9999 "I want to buy a sofa"
a2a send -a http://127.0.0.1:9999 "I want to buy a chair"
AX_LAB_CHAIR_TASK="chair 응답에 나온 Task 번호"
a2a send -a http://127.0.0.1:9999 --task-id "$AX_LAB_CHAIR_TASK" 100
a2a send -a http://127.0.0.1:9999 --task-id "$AX_LAB_CHAIR_TASK" 100
a2a send -a http://127.0.0.1:9999 --task-id "$AX_LAB_CHAIR_TASK" 100
a2a send -a http://127.0.0.1:9999 "I want to buy a bookshelf"
AX_LAB_SHELF_TASK="bookshelf 응답에 나온 Task 번호"
a2a task cancel -a http://127.0.0.1:9999 "$AX_LAB_SHELF_TASK"
```

버전 헤더가 없는 요청은 아래와 같다.

```bash
curl -s http://127.0.0.1:9999/ -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
  "params": {"message": {"role": "ROLE_USER", "messageId": "m0",
                        "parts": [{"text": "I want to buy a lamp"}]}}
}'
```

## 코드 출처와 보완한 부분

교재는 seller_agent.py의 핵심 부분을 공개했고 역제안·거절 처리 일부는 `...`로 생략했다. 내려받은 완전한 교수 원본을 실행했다고 주장하지 않는다. [seller_agent.py](seller_agent.py)는 공개된 구조와 체크포인트를 기준으로 보완한 수업용 구현이다.

시작가는 reserve의1.3배 올림이며 lamp/chair/bookshelf reserve는100/180/260이다. 역제안의 생략 부분은 교재 출력에 맞춰 lamp105, chair180, bookshelf260의 고정값으로 보완했다. reserve 이상의 가격은 수락하고 유효한 낮은 제안3회면 거절한다. 따라서 교재에 생략된 내부 정책을 유일하게 복원했다고 주장하지 않는다.

첫 구현은 CLI가 후속 메시지에 새 contextId를 만들 때 기존 task와 영수증 이벤트의 context가 달라져 실패했다. 교재의 `task=context.current_task`와 `TaskUpdater(task.id, task.context_id)` 구조로 수정했고, 별도의 context-id 플래그 없이 교재의 task-id 명령 그대로 성공했다. 실패는 [초기 CLI 로그](logs/lab-cli-initial.txt), [실패 요청](logs/lab-cli.txt), [서버 로그](logs/server.txt)와 최초 커밋1ef8e3d에 남겼다. 수정 후 성공 결과는 lab-cli-verified.txt다.

서버 한 모듈은 판매자 상태 전이만 소유하며199 pure LOC 범위다. 입출력은 SDK와 Pydantic 영수증 모델로 처리하고 Any·cast·타입 무시를 쓰지 않았다. RuffALL·basedpyrightall·format·스킬의 변경 파일 검사는 통과했다. 이 수업 실습은 실제 CLI 실행으로 검증했고 같은 구현을 따라 쓰는 별도 단위 테스트는 추가하지 않았다.

## 원문과 실행 근거

- [교수 6주차 LAB](https://wpti.dev/ai-agent-engineering-101/week-06.html)
- [공식 CLI 설치 안내](https://github.com/a2aproject/a2a-cli#installation)
- [환경 확인](logs/runtime.txt) · [설치 로그](logs/install-cli.txt) · [카드 원문](logs/card.json)
- [전체 성공 명령·원문 출력·종료코드](logs/lab-cli-verified.txt)
- [기존 24회 과제 보고서](../REPORT.md) · [기존 결과 원장](../results.csv)
