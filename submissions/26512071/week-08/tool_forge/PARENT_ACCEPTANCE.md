# 상위 실행 검증 결과

- 개발: 실제 Claude Code 2.1.306 / 정확한 `claude-opus-5-5`. 6개 개발 단계 모두 성공, 실제 assistant model 및 modelUsage 일치, fallback 없음. 원본 stream과 stage receipt는 `verification/`에 보존했다.
- 실행: 저장소 `.venv/Scripts/python.exe` (Python 3.12.10). `python -m unittest test_forge -v`: **14 tests OK** (최종 25.004초). RED→GREEN 3단계의 실제 실패/성공 로그를 보존했다.
- 데모: 잘못된 도구 → `testfailed:assertion_failed:1` → 수정 → `registered:add` → 호출 `5` → 새 인터프리터 복원/호출 `9`. 같은 library를 사용하는 두 번째 실행에서도 `tool_present_from_restore:add`와 정상 호출을 확인했다. LLM 루프 자체는 고정된 오프라인 데모이며, 라이브 모델/마켓 호출은 없다.
- 경계 실측: 악성 생성 코드/테스트 11건은 AST/정책 단계에서 거부, 등록·파일 게시·리스너 연결·외부 marker 쓰기 없음. 같은 플래그/최소 환경의 신뢰된 진단 자식은 실제 localhost 연결과 `example.com` DNS 조회에 성공했다. 따라서 **`os_network_isolation=false`** 이며 OS 샌드박스라고 주장하지 않는다.
- 기본 `mode="os"`는 `testfailed:os_sandbox_unavailable`로 거부한다. 사용 가능한 실행 경로는 명시적으로 선택하는 `mode="restricted"`의 수동 AST 해석 부분집합뿐이다. 임의 Python의 OS 수준 no-network/파일시스템/CPU/메모리/PID 격리는 제공하지 않는다. Docker 실행 파일은 PATH에 없고 `wsl --list --quiet`는 exit 0 / 빈 목록이었다.
- 성공 후에도 생성 파일을 import/exec하지 않는다. 등록 callable은 매 호출마다 별도 worker 프로세스로 RPC를 보낸다. 복원은 manifest/소스 해시 및 현재 정책/저장 테스트를 다시 검사한다. 해시는 인증 서명이 아니고 단일 writer 및 신뢰된 호스트 코드를 전제한다.
- 보호 대상 week-08 Python 소스 및 agent 설정/로컬 지침 **75개 SHA-256이 시작 시점과 모두 동일**했다. live seller/buyer/restock/cashpilot 수정·재시작·배포 없음. A2A/agentcard/forum/onchain/skill broadcast/toolbox/미사용 도구 제거 확장은 보류 그대로다. push/PR 없음.
- Windows checkout 보정: task-local `.gitattributes`로 LF를 고정했다. 실제 `git checkout-index`로 scratch에 이 폴더의 커밋 파일만 내보낸 뒤 **14 tests OK**(24.300초), 기존 영속 library 복원/데모 재실행까지 통과했다. `verification/committed-checkout-tests.json`에 해당 커밋·명령·결과를 보존했다.
- 소스와 검증 로그의 SHA-256, 모델 세션/실제 Write·Edit 대상, 실행 명령 및 결과는 `verification/FINAL_RECEIPT.json`에 있다. 상위의 코드 변경은 진단 주석에서 부정확한 `human-written`을 `task-authored`로 정정한 것뿐이며 실행 로직 변경은 없다.

## 재현

이 폴더에서 Git Bash로:

```bash
../../../../.venv/Scripts/python.exe -m unittest test_forge -v
../../../../.venv/Scripts/python.exe demo.py
../../../../.venv/Scripts/python.exe boundary_probe.py
```

판정: 독립 교육용 forge core/테스트/복원/호출 데모는 실행 검증 완료. 강의의 임의 Python을 실제 OS no-network 샌드박스에서 실행하는 백엔드는 **미제공**이며, 그 경로는 기본적으로 fail closed다. live market에 연결했다거나 과제 전체 제출을 완료했다는 뜻이 아니다.
