# Week 02 실행 방법

Python 3.10 이상, macOS/Linux(signal.SIGALRM 사용). 제출 폴더에서 다음을 실행한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -u run_openrouter.py --runs 3
```

키는 저장소 루트 또는 이 폴더의 로컬 `.env`에 넣거나 `OPENROUTER_API_KEY` / `OPENAI_API_KEY` 환경변수로 설정한다. `.env`는 키 한 줄 또는 변수 할당 형식을 지원하며 커밋하지 않는다. 실행 파일은 Anthropic 환경변수를 현재 프로세스에서 제거하고 OpenRouter를 사용한다. 모델 기본값은 `nvidia/nemotron-3.5-lightning:free`이며 재현 시 이 값을 유지한다. 도구 스키마는 tools_shared.py의 TOOL_SPECS, 패키지 및 실험 설정은 environment.json에 기록했다.

입력 app.log, TASK.md와 두 하네스는 원본 시작 코드 그대로다. 공통 모듈은 응답당 max_tokens=2048과 마지막 Meter 참조를 추가했다. 실행기는 양쪽에 동일한 180초 실행 제한 및 실시간 로그 보존을 적용한다. 1~4행은 제한 없는 파일럿이며 4행은 수동 중단의 부분 기록이다. 주 비교 대상은 같은 제한을 적용한 5~10행이다. 첫 실험은 ReAct 3회 다음 Plan-then-Execute 3회 순서로 실행한다. 재실행 시 results.csv와 logs를 삭제하지 않고 추가 기록한다. 모델 서비스 상태, 공급자 기본 샘플링 및 실행 순서 때문에 결과의 완전한 일치는 보장하지 않는다.

iters는 도구 호출 횟수가 아니라 Meter에 기록된 모델 응답 횟수다. tokens는 응답의 입력·출력 토큰 합산이다. 초기 중단 실행(4행)의 토큰·반복은 회수하지 못해 빈칸이며 0이 아니다. 개선한 실행기는 오류/시간 초과 시 완료된 응답까지의 Meter를 기록한다. 이 숫자는 진행 중이던 요청의 사용량을 포함하지 않는 하한이다. 4행의 interventions=1은 수동 중단이며, 다른 행의 도구 승인 횟수와 구분한다. 성공 판정은 최종 답변에 TASK.md의 expected 문자열이 포함되는지 검사하므로 엄격한 정답 검증보다 느슨하다. 로그의 도구 출력은 시작 코드에서 200자, 일부 단계와 최종 답변은 300자로 제한해 기록한다.

저장소 루트에서 제출 검사:

```bash
python3 scripts/check_week02.py submissions/26510122/week-02
```

Codex와 함께 실행 및 분석했다. REPORT.md는 측정에 기반한 검토용 초안으로, 학생 본인이 로그와 코드를 확인하고 해석을 검토해야 한다.
