# 실행 준비

## 현재 상태

- 시나리오 4개를 첫 실행 전에 고정함.
- 모델 후보는 3주차와 같은 `gpt-4.1-nano-2025-04-14`, temperature 0.
- 아직 실제 API 호출과 결과 생성은 하지 않음.

## 예정 명령

```bash
./run_lab.sh --dry-run
AX_LAB_ENV_FILE="$HOME/.config/ax-agent/openai.env" ./run_lab.sh --allow-paid
cd ../../..
python scripts/check_week04.py submissions/25620027/week-04
```

`run_lab.sh`는 완료된 `(run, condition, scenario)`를 건너뛰어 중단 지점부터 재개한다.
API 키와 환경 파일은 커밋하지 않는다.
