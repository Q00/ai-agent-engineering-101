# DeepSeek V4.1 Flash로 반복한 샷건 실험

`../armed_tool/`의 Luna 다섯 묶음을 같은 프로토콜로 DeepSeek V4.1 Flash에 돌린다. 기존 실행기들은 완료된 실험의
해시 검증 때문에 수정하지 않고, `run_deepseek.py`가 격리 사본을 불러 모델 설정(`config.json`)만 바꾼다.

```sh
python3 -m unittest discover -s submissions/26622007/week-04/deepseek_compare -p 'test_*.py' -v
python3 -u submissions/26622007/week-04/deepseek_compare/run_deepseek.py --design control --jobs 20 --env-file submissions/26622007/.env
# 같은 명령을 --design shotgun-auto / shotgun-forced / holding-with-tool / holding-only로 (5개 × 20 = 병렬도 100)
python3 submissions/26622007/week-04/deepseek_compare/compare_models.py
python3 submissions/26622007/week-04/deepseek_compare/threats.py
```

작업 단위는 에피소드이고 로그는 `../logs/<run>-s<scenario>.jsonl/.txt`다. 결과는 `runs/deepseek-<design>-ep-20260928/`,
모델 비교는 `runs/model-comparison.csv`, 위협 분류는 `threat_labels.csv`(수작업)와 `runs/threats.csv`에 있다.
병렬도 2로 시작했다가 중단한 첫 실행은 `runs/deepseek-<design>-20260928/`에 남겼다. 해석은 [REPORT.md](REPORT.md).
