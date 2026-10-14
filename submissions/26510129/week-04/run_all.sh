#!/usr/bin/env bash
# 9개의 run. 조건 하나 x 반복 3번. 로그 파일 하나가 run 하나다.
#
# API 키는 환경변수로만 준다. 이 파일에도, 어느 .py에도 키를 적지 않는다.
#   export OPENAI_API_KEY=...      # 본인 키
#   unset OPENAI_BASE_URL          # OpenAI 직접 호출
#   export AGENT_MODEL=gpt-4o-mini
#   export AGENT_TEMPERATURE=0
#
# 중간에 끊겨도 같은 명령을 다시 돌리면 results.csv에 있는 (run, scenario)를 건너뛴다.
# 이어 돌릴 때 로그가 덮이지 않도록 tee -a를 쓴다.
set -u
cd "$(dirname "$0")"
mkdir -p logs

for condition in free tagged structured; do
  for repeat in 1 2 3; do
    run=$(printf "%s-%02d" "$condition" "$repeat")
    echo "=== $run ==="
    python3 negotiate.py --condition "$condition" --repeat "$repeat" 2>&1 | tee -a "logs/$run.txt"
  done
done

echo "=== summary ==="
python3 summarize.py
