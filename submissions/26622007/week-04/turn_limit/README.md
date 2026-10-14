# 턴 제한 비교 실험

기준은 `html-deepseek-20260922`의 8턴 제한 36개 에피소드다. 추가 실험은 같은
네 시나리오 × 세 조건 × 세 반복을 새로 실행한다. 원래 runner와 결과를 보존하고
별도 확장 runner가 원래 프롬프트·파서·전송기를 재사용한다.

`max_turns=null`, HTTP 요청 횟수 상한도 없다. 유효한 수락이면 `deal`, `refuse`이면
`no_deal`로 종료한다. API 오류는 `crashed`, 에피소드당 180초가 지나도 미종료이면
`censored`로 기록하며 outcome/correct/violation은 비워 둔다. 관측 시간은 다음 발언
시작 전에 확인하므로 진행 중인 발언과 reader 호출은 완료한다. 실제 시간은 180초를
넘을 수 있다. 시간 관측 중단을 `no_deal`이나 8턴 제한의 `open`으로 바꾸지 않는다.

모델·온도·top_p·provider·reasoning·전체 이력·response_format·파싱·가격 판정은
기준 실험과 같다. 두 묶음은 독립 표본이며 seed가 없어 개별 결과 차이를 턴 제한만의
인과 효과로 단정할 수 없다. 추가로 무제한 실행의 첫 8턴을 같은 파서로 재판정해
동일 대화에서 8턴 이후에 종료된 건수를 기록한다. 모델은 턴 제한을 안내받지 않는다.

```sh
python3 -m unittest discover -s submissions/26622007/week-04/turn_limit -p 'test_*.py' -v
python3 -u submissions/26622007/week-04/turn_limit/run_unlimited.py --suite unlimited-deepseek-20260922 --env-file submissions/26622007/.env --jobs 3 --observation-seconds 180
python3 submissions/26622007/week-04/turn_limit/compare.py --suite unlimited-deepseek-20260922
```

원본 로그는 `../logs/<run>.jsonl` 및 `.txt`, 결과는 `runs/<suite>/results.csv`,
소스·설정·프롬프트·실행 정책은 `runs/<suite>/manifest.json`에 기록한다.
완료·관측 중단·실패 모두 기존 행으로 보존하며 재개 시 중복 호출하지 않는다.
과제의 원래 `../results.csv`에는 이 확장 실험을 섞지 않는다.
