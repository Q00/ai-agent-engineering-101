"""Run offline regression checks and preserve a new, unedited console log per run."""
import datetime
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
checks = [
    ("seller", [sys.executable,"-B","-m","unittest","discover","-s","tests","-v"]),
    ("restock", [sys.executable,"-B","-m","unittest","discover","-s","tests","-v"]),
    ("buyer-forum", [sys.executable,"-B","-m","unittest","test_forum","-v"]),
    (".", [sys.executable,"-B","-m","unittest","test_runtime","test_participation_evidence","-v"]),
    (".", ["npm","run","typecheck"]),
    (".", ["npm","run","test:buyer"]),
    (".", [sys.executable,"-B","-c", "import asyncio,httpx,app; "
           "exec('async def check():\\n async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app),base_url=\"http://localhost\") as c:\\n  r=await c.get(\"/.well-known/agent-card.json\"); assert r.status_code==200 and \"No warranty\" in r.text\\n'); "
           "asyncio.run(check()); print('ASGI card smoke check passed')"]),
]


def main():
    (ROOT / "logs").mkdir(exist_ok=True)
    path = ROOT / "logs" / ("offline-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".log")
    failed = []
    with path.open("x") as log:
        for directory, command in checks:
            log.write(f"\n$ cwd={directory} {command!r}\n")
            log.flush()
            try:
                result = subprocess.run(command,cwd=ROOT/directory,
                    env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"},stdout=log,stderr=subprocess.STDOUT,timeout=90)
                status = result.returncode
            except subprocess.TimeoutExpired:
                status = 124
            log.write(f"exit={status}\n")
            if status:
                failed.append(command)
            print(f"{directory}: {'PASS' if status == 0 else 'FAIL'} {command[0]}",flush=True)
    print(f"Console log: {path}")
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
