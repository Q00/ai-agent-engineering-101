# 기본 Contract Net 최종 배정 실험 규약

복합 과제 peer DAG 45회와 구분하여 고정 manager의 원래 강의 조건을 확인한다.
코드·이 설정·현재 tasks.json의 5개 입력/gold를 커밋한 뒤 실행한다.
기존 결과 5행(과거 6개 입력과 다른 설정)은 보존하고 같은 반복 실험에 합산하지 않는다.

- baseline/homogeneous/overconfident 각 3회, 순서 BHO/HOB/OBH. 회차마다 동일 5개 작업과 A/B/C 입찰.
- 기존 contract_net.py의 프롬프트·배정·메시지 집계 방식 유지. 실제 업무 수행 없이 담당자 적합성만 입찰한다.
- 현재 기본 config의 model/provider/temperature/max_tokens=512 유지. 명시적인 strict response_format 그대로.
- 별도 config.json에서 HTTP429를 peer와 같은 최대 6회 정책으로 맞추고 전체 HTTP 요청 상한 810=5*3*9*6을 명시한다.
- API 논리 호출 예상 135회. 외부 peer 실험이 종료된 뒤 실행하며 실행 간 15초 대기한다.
- run_one의 원본 로그와 results.csv 기록을 사용한다. 실패는 수치를 비운 crashed 행으로 남기고 해당 회차를 교체하지 않는다. 다음 예정 회차는 계속 수행한다.
- gold는 기존 역할 일치 정답이며 작업 산출물 품질의 정답이 아니다. 모든 조건에서 입력/gold/모델/설정은 같다.
- 메시지 집계는 기존 코드대로 공고+bid=true인 유효 입찰+낙찰이며 bid=false 거절은 별도 계수다. HTTP 요청/응답 수와 다르다.
- 사용자에게는 관측 결과·로그 근거를 제공한다. Smith 원문 비교와 최종 과제 해석은 사용자가 검토하여 작성한다.

실행(저장소 루트): `submissions/26622007/.venv/bin/python -u submissions/26622007/week-03/task_sets/complex-final/allocation_study.py run`.
이 명령은 원래 results.csv에 추가한다. 기존 run.py 기본 설정을 바꾸지 않으며 별도 설정 경로는 각 start 로그에 실제 config로 저장된다.
