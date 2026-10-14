# 한국어 협상 재현

```sh
python3 -u submissions/26622007/week-04/language/run_korean.py --env-file submissions/26622007/.env --jobs 3
uv venv submissions/26622007/course_checks/.venv-stats
uv pip install --python submissions/26622007/course_checks/.venv-stats/bin/python -r submissions/26622007/week-04/language/requirements.txt
submissions/26622007/course_checks/.venv-stats/bin/python -m unittest discover -s submissions/26622007/week-04/language -p 'test_*.py' -v
submissions/26622007/course_checks/.venv-stats/bin/python submissions/26622007/week-04/language/analyze.py
python3 submissions/26622007/week-04/language/verify.py
```

가상 환경은 공식 검사기가 탐색하는 week-04 밖에 둔다. 실행은 표준 라이브러리만
쓰고 통계 분석만 명시한 SciPy/statsmodels 환경을 사용한다. API 키는 출력하거나 저장하지 않는다.

`run_korean.py`는 총 72개의 고정된 에피소드를 수행하고, 기록된 완료·실패·관측 중단
행은 재실행하지 않는다. 코드·계획·설정 해시가 다르면 같은 실험을 재개하지 않는다.
한국어 프롬프트는 `korean.py`, 수집 전에 고정한 방법은 `ANALYSIS_PLAN.md`다.

통계 분석은 기존 영어 72회와 새 한국어 72회의 총 144개 에피소드를 사용한다.
에피소드 내 API 호출이나 개별 발언을 독립 통계 표본으로 세지 않는다.
단일 실행의 종료는 `deal`/`no_deal`; 8턴 미종료는 `open`, 무제한 180초 관측 중단은
`censored`다. 원본의 구조화 출력 및 파싱 규칙을 유지한다.

결과: [보고서](REPORT.md), [한국어 대화 사례](DIALOGUES.md),
[원본 CSV](runs/korean-deepseek-20260922/results.csv),
[모든 검정](runs/korean-deepseek-20260922/tests.csv).
모델이 추가한 화폐 단위나 가짜 이전 대화도 원문 그대로 남기고 자동 보정하지 않는다.

`verify.py`는 완료된 72회 자료에서만 실행한다. API 클라이언트 생성을 금지한 상태로
재개해 새 호출이 없음을 확인하고, 원본 결과·manifest·대화 로그 38개의 해시가
유지되는지 검사한다. 기존 환경 파일의 키 값이 산출물에 포함되지 않았는지도 검사한다.
