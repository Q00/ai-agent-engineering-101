# Week 04 HTML 실습 실행

강의 HTML의 `acl.py`, `negotiate.py`, 탁상등 참조 로그와 다섯 가지 실패 사례를 기준으로
실습을 실행한다. `reference/`는 upstream 커밋에 고정한 과제 안내와 코드 조각이다.
같은 커밋의 검사기는 `submissions/26622007/course_checks/check_week04_51c09f4.py`에 있다.
검사기 자체의 키 탐지 문자열이 검사 대상에 포함되지 않도록 week-04 밖에 보관한다.
공개된 코드는 완성된 starter가 아니며 역할·reader 문구의 `...`는 생략 표시다.
공개 문구를 사용하고 생략 표시는 제거했다. seller 문장 연결 외에 협상 전략이나
가격 선택 요령을 추가하지 않는다. 초기화, 파서, CSV·로그·재개는 공개 명세를 구현했다.

## 실행

```sh
python3 submissions/26622007/week-04/lab/experiment.py --suite html-deepseek-20260922 --env-file submissions/26622007/.env --jobs 3
python3 -m unittest discover -s submissions/26622007/week-04/lab -p 'test_*.py' -v
python3 submissions/26622007/course_checks/check_week04_51c09f4.py submissions/26622007/week-04
```

동일 명령은 기록된 `(run, scenario)`를 건너뛰며 재개한다. 실패 행도 지우지 않는다.
코드·설정·시나리오 해시가 다르면 같은 suite를 재개하지 않는다. 새 suite는 새 실험이다.
`--jobs 3`은 독립 run 세 개만 동시에 실행한다. 에피소드 내부는 구매자부터 순차 교대하며
대화 이력과 API 클라이언트·로그는 run마다 분리한다. 샘플링 seed는 별도 설정하지 않는다.

## 고정 설정과 출처 대응

- 강의의 세 조건, 네 화행, 역할별 private limit, 최대 8개 메시지, 전체 대화 이력을 사용한다.
- 자전거(120/150), 교재(40/40), 키보드(90/70)는 HTML scenarios.json에 있다.
  탁상등(30/45)은 HTML의 `logs/free-03.txt, scenario 2` 사례에 있다. 숫자는 reserve/budget이다.
- 각 조건에서 네 시나리오를 세 번씩 실행한다. 결과 36행, run별 콘솔 9개가 생긴다.
- free는 같은 모델·온도의 reader가 전체 대화를 보고 마지막 메시지를 분류한다.
- tagged는 선두 태그를 정규식으로 읽고 propose일 때만 동일 reader에서 가격을 얻는다.
  reader가 출력하는 화행은 무시하고 태그를 따른다. reject-proposal 뒤의 역제안은 등록하지 않는다.
- structured는 선두 JSON만 파싱한다. JSON 뒤의 자연어 가격은 사용하지 않는다.
- reader가 잘못 읽은 가격도 보정하지 않는다. 수락은 상대의 마지막 기록된 제안에 연결하고,
  유효 제안 없는 수락은 format_errors로 기록하고 계속한다. 강의 참조 결과도 해당 수락으로
  거래가 성립하지 않았다고 설명한다.
- 한도 밖 거래는 차단하지 않고 violation으로 기록한다. 제한까지 미종료면 open이다.

모델은 사용자 선택인 DeepSeek V4.1 Flash, OpenRouter DeepInfra FP8이다.
앞서 확정한 temperature=1.0, top_p=0.95, reasoning off를 모든 역할에 동일 적용한다.
로컬 사용자 규칙에 따라 모든 요청에 response_format을 명시한다. free/tagged 협상은 text,
reader와 structured 협상은 strict JSON Schema다. 이는 HTML 참조 실행에 명시되지 않은
API 수준 형식 제약이며, 형식 오류율을 Claude CLI 참조 결과와 직접 비교할 수 없다는
제약으로 기록한다. JSON의 필드·타입·가격·선행 제안 의존성은 로컬에서도 검증한다.

실험의 원본 요청·응답과 판독은 상위 `logs/`, 집계 입력은 상위 `results.csv`,
소스 커밋·해시·프롬프트는 `runs/<suite>/manifest.json`에 보존한다.
이전 `pilot/` 실험은 이 집계에 포함하지 않는다.
