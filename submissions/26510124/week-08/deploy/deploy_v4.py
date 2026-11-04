"""Run from the Mac terminal: one SSH password prompt, then staged deployment.

--prepare-only creates a source archive without connecting or starting agents.
No private key, environment, local memory, or historical log is uploaded.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "agent-market-week07/repo/submissions/26510124/week-08"
CONFIGS = ("agent.json", "model-profile.json", "buyer-policy.json", "feedback-config.json", "advisor-config.json", "shop-policy.json",
           "shop-adaptive.json", "shop-round2.json")


def source_files(root):
    files = []
    for pattern in ("*.py", *CONFIGS, "README.md", "requirements.txt", "requirements-lock.txt", ".gitignore",
                    "prompts/*.txt", "vendor/market_kit_v4.py", "vendor/SOURCE.json", "deploy/*.py"):
        for path in root.glob(pattern):
            if path.name.startswith(".") and path.name != ".gitignore":
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("Expected a regular source file: " + str(path))
            files.append(path)
    return sorted(set(files))


def prepare(root=ROOT):
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = root / ".market" / ("deploy-" + run)
    out.mkdir(parents=True, mode=0o700)
    files = source_files(root)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "run": run,
                "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    archive = out / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for path in files:
            bundle.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        raw = (json.dumps(manifest, indent=2) + "\n").encode()
        info = tarfile.TarInfo("release-manifest.json")
        info.size, info.mode = len(raw), 0o600
        bundle.addfile(info, io.BytesIO(raw))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    evidence = root / "logs" / ("deployment-" + run)
    evidence.mkdir(parents=True)
    (evidence / "package-manifest.json").write_text(json.dumps({**manifest, "archive_sha256": digest}, indent=2) + "\n")
    print(f"Prepared {len(files)} source files; no keys, credentials or live state.", flush=True)
    print("Archive: " + str(archive), flush=True)
    return run, archive, digest, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    run, archive, digest, evidence = prepare()
    if args.prepare_only:
        return 0
    # Short private socket path: repository paths can exceed Unix socket limits.
    with tempfile.TemporaryDirectory(prefix="week08-ssh-") as tmp:
        socket = str(Path(tmp) / "control")
        host = "daintI"
        ssh = ["ssh", "-S", socket, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host]
        connected = False
        try:
            print("서버 SSH 비밀번호가 필요하면 이 터미널에 입력하세요. 입력 문자는 표시되지 않습니다.", flush=True)
            subprocess.run(["ssh", "-M", "-S", socket, "-o", "ControlPersist=600", "-o", "ConnectTimeout=10",
                            "-o", "NumberOfPasswordPrompts=1", "-fN", host], check=True)
            connected = True
            remote_dir = f"{REMOTE}/.market/incoming/{run}"
            subprocess.run(ssh + ["umask 077; mkdir -p " + shlex.quote(remote_dir)], check=True)
            subprocess.run(["scp", "-o", "ControlPath=" + socket, "-o", "BatchMode=yes",
                            str(archive), str(ROOT / "deploy/install_server.py"), host + ":" + remote_dir + "/"], check=True)
            command = shlex.join(["python3", remote_dir + "/install_server.py", "--archive",
                                  remote_dir + "/source.tar.gz", "--sha256", digest, "--run", run])
            # Stream progress and keep an unedited local copy. The installer never prints secrets.
            with (evidence / "server-deployment.log").open("x") as log:
                process = subprocess.Popen(ssh + [command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, bufsize=1)
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                code = process.wait()
                log.write(f"SSH/INSTALL EXIT: {code}\n")
            if code:
                print("Deployment was not confirmed. Read the saved log: " + str(evidence), flush=True)
                return code
            print("v4 전환과 가게·구매자 등록, ACP 응답 확인이 완료됐습니다.", flush=True)
            return 0
        except KeyboardInterrupt:
            print("\nInterrupted. Deployment is not confirmed; check the server service before retrying.", flush=True)
            return 130
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Transfer/authentication failed: {exc}; no success is claimed.", flush=True)
            return getattr(exc, "returncode", 1)
        finally:
            if connected:
                subprocess.run(["ssh", "-S", socket, "-O", "exit", host],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
