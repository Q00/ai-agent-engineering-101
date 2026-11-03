"""Reuse existing market identity, never overwrite keys or copy the API credential."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen
from r3.state import ROOT
from r3.model import api_key


def prepare():
    previous = ROOT.parent / "week-07"
    results = []
    for name in ("shop_key.pem", "buyer_key.pem", ".market/owner.env"):
        src, dst = previous / name, ROOT / name
        if not dst.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError("identity destination escaped the student directory")
        ignored = subprocess.run(['git', 'check-ignore', '--quiet', str(dst)], cwd=ROOT.parents[2]).returncode == 0
        if not ignored:
            raise RuntimeError("credential destination is not Git-ignored")
        if dst.exists():
            if src.exists() and src.read_bytes() != dst.read_bytes():
                raise RuntimeError("existing identities differ; refusing to replace either key")
            results.append({"path": name, "status": "existing_identity_preserved"})
        elif src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            results.append({"path": name, "status": "copied_existing_identity_into_ignored_path"})
        else:
            results.append({"path": name, "status": "missing"})
    report = {"identity": results, "api_key_present": bool(api_key()), "interpreter": sys.executable,
              "api_secret_copied": False, "public_market_joined": False}
    try:
        with urlopen(Request('https://market.ourolabs.ai/api/board', headers={'User-Agent': 'R3-readiness/1.0'}), timeout=15) as r:
            board = json.loads(r.read())
        report['public_market_round'] = board.get('current')
    except Exception as exc:
        report['public_check_error'] = type(exc).__name__
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    p = ROOT / 'logs' / ('runtime-preparation-' + stamp + '.json')
    with p.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(report, log=str(p)), ensure_ascii=False, indent=2))
    return 0 if all(x['status'] != 'missing' for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(prepare())
