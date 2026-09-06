# 작업 과정

- 사용자 요청: 폴더의 과제를 정리한 뒤 함께 시작하기. 학번 26510122 제공.
- Codex 작업: OpenAI 호환 시작 코드 복사, write_note 구현, 도구 스키마와 설명 초안 작성, 지출 예제 입력 준비.
- 설계 선택: 파일 경로를 모델이 지정하지 않고 expense_summary.txt로 고정. 기존 결과를 덮어쓰지 않고 추가. 심볼릭 링크로 다른 파일을 수정하는 경우를 차단.
- 환경 제약: 시작 시 API 키와 openai 패키지가 없어 실제 모델 실행은 미실시. SDK import를 run 내부로 이동하여 도구 함수는 SDK 없이 검증 가능하게 함.
- 남은 학습 작업: 실제 실행에서 도구 선택 관찰, 설명의 효과 평가, 자신의 판단으로 TOOLS.md 수정.
- 오프라인 검증 통과: 도구 3개 등록, 입력 읽기, 합계와 미정산액 계산, 기존 메모 보존, 심볼릭 링크 거부. 임시 디렉터리에서 검증했으며 실제 모델 실행 로그로 제출하지 않음.
- 제출 검사 결과: 파이썬 구문, 도구 개수, TOOLS.md 통과. 실제 실행 로그가 없어 logs 검사 1건 실패. API 설정 후 전체 실행 필요.

## OpenRouter 실제 실행

- 사용자가 저장소 루트 .env에 키가 있다고 알려주어 값을 출력하지 않고 로드했다.
- OpenRouter 모델 목록 API에서 도구 지원과 무료 요금을 확인한 nvidia/nemotron-3.5-lightning:free를 사용했다.
- run-01: read_file → calculator(48000 + 9500) → calculator(57500 - 12000) → write_note. 총지출 57500원, 미정산액 45500원 저장에 성공했다.
- run-02: 저장하지 말라는 계산 요청에는 calculator만 호출하고 45500을 반환했다. write_note 호출이 없고 기존 요약은 유지됐다.
- 두 실행은 요청 자체가 다르므로 도구 추가 전후의 통제된 비교 실험은 아니다. 설명 문구의 인과 효과까지 증명하지는 않는다.
- 두 번째 실행 명령: `.venv/bin/python -u run_openrouter.py "Use calculator to calculate 48000 + 9500 - 12000. Only answer the amount; do not save a note or write any file."`
