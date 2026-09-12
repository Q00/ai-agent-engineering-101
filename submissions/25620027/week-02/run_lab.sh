#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mode="${1:-help}"
if [[ $# -gt 0 ]]; then shift; fi

runtime=(uv run --no-project --python 3.12.11 --with openai==3.8.0 --with anthropic==1.4.0)
launch=(env)
env_file="${AX_LAB_ENV_FILE-${HOME}/.config/ax-agent/openai.env}"
if [[ -n "$env_file" && -f "$env_file" ]]; then
  launch=(env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY -u OPENAI_BASE_URL -u AGENT_MODEL)
  runtime+=(--env-file "$env_file")
elif [[ -n "${AX_LAB_ENV_FILE:-}" ]]; then
  printf '%s\n' '지정한 환경변수 파일을 찾을 수 없습니다.' >&2
  exit 2
fi

case "$mode" in
  check)
    "${launch[@]}" "${runtime[@]}" python -c '
import importlib.metadata
import os
import sys
from openai import OpenAI
from anthropic import Anthropic
from run_ab import read_task
from tools_shared import read_file, count_pattern
from harness_plan_execute import parse_plan

task, expected = read_task()
assert expected == "14:00"
assert len(read_file("app.log").splitlines()) == 60
assert count_pattern("app.log", r"^2026-09-01 14:.* ERROR ") == "6"
assert parse_plan("[\"read input\", \"count errors\"]") == ["read input", "count errors"]
assert parse_plan("invalid plan") is None
print("Python:", sys.version.split()[0])
print("openai:", importlib.metadata.version("openai"))
print("anthropic:", importlib.metadata.version("anthropic"))
print("API key:", "SET" if os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY") else "NOT SET")
print("준비 확인 완료: 모듈 불러오기, 입력 파일, 도구 실행, 계획 파싱")
print("실제 모델 호출과 실험 결과 생성은 하지 않았습니다.")
'
    ;;
  run)
    "${launch[@]}" "${runtime[@]}" python -c '
import os
import runpy
import sys

if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
    print("API 키 환경변수가 아직 설정되지 않았습니다. SETUP.md의 연결 설정을 확인하세요.", file=sys.stderr)
    sys.exit(2)
sys.argv[0] = "run_ab.py"
runpy.run_path("run_ab.py", run_name="__main__")
' "$@"
    ;;
  submission-check)
    "${launch[@]}" "${runtime[@]}" python ../../../scripts/check_week02.py .
    ;;
  help|--help|-h)
    printf '%s\n' \
      './run_lab.sh check                 수업 전 준비 확인 (모델 호출 없음)' \
      './run_lab.sh run --runs 3          두 하네스 각 3회 실험' \
      './run_lab.sh submission-check      제출 조건 검사' \
      '모델 연결 설정과 실습 흐름: SETUP.md'
    ;;
  *)
    printf '지원하지 않는 명령: %s\n' "$mode" >&2
    exit 2
    ;;
esac
