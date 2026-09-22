"""Week 02 — 실험 조건 기록기.

모델 출력은 확률적이므로 지표만 남기면 재현이 안 된다. 이 모듈은 한 배치를
돌릴 때의 조건 일체를 모아서 (1) 안정적인 지문(fingerprint)으로 압축하고,
(2) `conditions/<지문>.json`에 저장하고, (3) 각 런 로그 맨 위에 붙일 배너를
만든다. 지문이 같으면 조건이 같고, 조건을 바꾸면 지문이 바뀐다.

지문이 덮는 것: 제공자, 모델, 도구 스키마, 시스템 프롬프트, 하네스 상한
(max_steps / max_replan / max_tool_rounds), 과제와 성공 기준.
지문이 덮지 않는 것: 실행 시각, git 커밋, 파이썬·패키지 버전 — 조건이 아니라
관측 시점의 부수 정보이므로 `observed` 목록에 따로 쌓는다.

API 키는 절대 기록하지 않는다. 어떤 환경변수가 설정되어 있는지(이름)만 남긴다.
"""
import hashlib
import inspect
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import harness_plan_execute
import harness_react
import tools_shared

CONDITIONS_DIR = "conditions"

# 값이 아니라 "설정되어 있는지"만 기록할 환경변수 (키 유출 방지)
SECRET_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
# 값을 그대로 기록해도 안전한 환경변수
PUBLIC_ENV = ("OPENAI_BASE_URL", "AGENT_MODEL")


def _defaults(fn) -> dict:
    """함수 시그니처의 기본값 인자만 뽑는다. 하네스의 상한을 바꾸면 기록도
    따라 바뀌도록 하드코딩하지 않고 시그니처에서 읽는다."""
    return {name: p.default
            for name, p in inspect.signature(fn).parameters.items()
            if p.default is not inspect.Parameter.empty and name != "log"}


def _git() -> dict:
    def run(*args):
        try:
            return subprocess.run(args, capture_output=True, text=True,
                                  timeout=5).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    return {"commit": run("git", "rev-parse", "--short", "HEAD"),
            "dirty": bool(run("git", "status", "--porcelain", "."))}


def _packages() -> dict:
    out = {}
    for name in ("openai", "anthropic"):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, "__version__", "unknown")
        except ImportError:
            pass
    return out


def collect(task: str, expected: str, runs_per_harness: int) -> dict:
    """조건(지문 대상)과 관측 정보(부수 정보)를 함께 담은 레코드를 만든다."""
    cond = {
        "provider": tools_shared.PROVIDER,
        "model": tools_shared.MODEL,
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "task": task,
        "expected": expected,
        "tools": tools_shared.TOOL_SPECS,
        "prompts": {
            "react_system": harness_react.SYSTEM,
            "plan_system": harness_plan_execute.SYSTEM_PLAN,
            "exec_system": harness_plan_execute.SYSTEM_EXEC,
        },
        "limits": {
            "react": _defaults(harness_react.run_react),
            "plan_exec": _defaults(harness_plan_execute.run_plan_execute),
        },
        "irreversible_tools": sorted(harness_react.IRREVERSIBLE),
    }
    observed = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "runs_per_harness": runs_per_harness,
        "git": _git(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": _packages(),
        "env_set": {k: bool(os.environ.get(k)) for k in SECRET_ENV},
        "env": {k: os.environ.get(k) for k in PUBLIC_ENV},
    }
    return {"fingerprint": fingerprint(cond), "conditions": cond,
            "observed": [observed]}


def fingerprint(cond: dict) -> str:
    """조건 딕셔너리의 정규화 JSON에 대한 sha1 앞 8자리. 조건이 같으면 같은 값."""
    blob = json.dumps(cond, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:8]


def save(record: dict, base: str = ".") -> Path:
    """`conditions/<지문>.json`에 기록한다. 같은 조건으로 다시 돌리면 파일을
    덮지 않고 `observed`에 이번 배치를 덧붙인다."""
    path = Path(base, CONDITIONS_DIR, f"{record['fingerprint']}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            record = {**record, "observed": old.get("observed", []) + record["observed"]}
        except (json.JSONDecodeError, OSError):
            pass                        # 읽을 수 없으면 이번 배치로 새로 쓴다
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def banner(record: dict, harness: str, run_no: int) -> list:
    """런 로그 맨 위에 붙는 조건 배너. 로그 파일 하나만 봐도 어떤 조건에서
    나온 결과인지 알 수 있게 한다."""
    c = record["conditions"]
    obs = record["observed"][-1]
    limits = " ".join(f"{k}={v}" for k, v in c["limits"][harness].items())
    return [
        f"[cond] run={run_no} harness={harness} fingerprint={record['fingerprint']}",
        f"[cond] provider={c['provider']} model={c['model']} base_url={c['base_url']}",
        f"[cond] tools={[t['name'] for t in c['tools']]} irreversible={c['irreversible_tools']}",
        f"[cond] limits {limits}",
        f"[cond] expected={c['expected']!r} at={obs['at']} git={obs['git']['commit']}"
        f"{'+dirty' if obs['git']['dirty'] else ''}",
        f"[cond] 전체 조건: {CONDITIONS_DIR}/{record['fingerprint']}.json",
    ]


def note(record: dict, harness: str) -> str:
    """results.csv의 note 칼럼에 넣을 짧은 조건 표시. 헤더는 고정이라
    조건을 담을 수 있는 칼럼은 note 하나뿐이다."""
    limits = " ".join(f"{k}={v}" for k, v in record["conditions"]["limits"][harness].items())
    return f"cond={record['fingerprint']} {limits}"


if __name__ == "__main__":                # 키 없이 조건만 확인할 때
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    rec = collect("(dry run)", "(dry run)", 0)
    print(json.dumps(rec, indent=2, ensure_ascii=False))
