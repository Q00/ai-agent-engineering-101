# 실행 준비

## 현재 상태

- 시나리오 4개를 첫 실행 전에 고정함.
- 모델은 3주차와 같은 `gpt-4.1-nano-2025-04-14`, temperature 0.
- 실제 실행 결과는 `results.csv`, 발화와 해석 원문은 `logs/`, 분석은 `REPORT.md`에 기록한다.
- 2026-09-22: 36개 에피소드 완료, 원문 로그 9개. 실행 증거 커밋은 `79301cc`.
- 공식 `check_week04.py` 통과. CSV 36행과 보고서 표, 로그의 발화·reader 횟수 일치를 별도 확인했다.

## 실행·검사 명령

```bash
./run_lab.sh --dry-run
AX_LAB_ENV_FILE="$HOME/.config/ax-agent/openai.env" ./run_lab.sh --allow-paid
cd ../../..
python scripts/check_week04.py submissions/25620027/week-04
```

`run_lab.sh`는 완료된 `(run, condition, scenario)`를 건너뛰어 중단 지점부터 재개한다.
API 키와 환경 파일은 커밋하지 않는다.

새 실험은 별도 폴더로 기록한다.

```bash
./run_lab.sh --allow-paid --output /tmp/week04-reproduction
```

기존 결과가 있는 폴더에서는 설정을 변경하지 않는다. 의존성은 `uv.lock`을 사용한다.
