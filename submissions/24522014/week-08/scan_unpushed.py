"""Read-only scan for secrets in what a push would publish: lines added by each unpushed commit, and any files named on the command line.

Output is commit, file, line and kind. A value never reaches the screen or the report: --triage shows context with the token masked.
Kinds: exact (a real local secret, compared in memory), shape (a token that looks real), mention (the word only, usually docs or code).
"""

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "logs" / "submission-scan"
LOW_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
HUNK = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
SECRET_ENV = re.compile(r"key|token|secret|passw|credential|owner", re.I)
SECRET_FIELD = re.compile(r"key|token|secret|passw|credential|bearer|private|seed|mnemonic|cookie|owner|claim_?code", re.I)
PUBLIC_FIELDS = {"message_key", "market_pubkey", "key", "pubkey", "public_key", "pub", "owner"}
SKIP_ENV = {"SSLKEYLOGFILE"}
MIN_SECRET = 12

SHAPES = {
    "openai_or_anthropic_key": rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}",
    "google_key": rb"AIza[0-9A-Za-z_-]{30,}",
    "github_token": rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}",
    "aws_key": rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}",
    "jwt": rb"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "bearer_value": rb"(?i)bearer[ :=\"']+[A-Za-z0-9._~+/-]{16,}=*",
    "private_key_block": rb"-----BEGIN [A-Z ]*PRIVATE KEY-----\s*[A-Za-z0-9+/=]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----\s*$",
    "env_assignment": rb"(?m)^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)\s*=\s*[\"']?[^\s\"'$<{%]{8,}",
    "json_secret_field": rb"(?i)[\"'][a-z_-]*(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|private[_-]?key|claim[_-]?code)[\"']\s*[:=]\s*[\"'][^\"'\s<{$]{8,}[\"']",
    "auth_header": rb"(?i)[\"']?(?:authorization|x-api-key|x-goog-api-key|api-key|proxy-authorization)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+|basic\s+)?[A-Za-z0-9._~+/-]{16,}",
    "owner_argument": rb"--owner[ =][\"']?(?!CODE\b)[A-Za-z0-9_-]{6,}|MARKET_OWNER\s*=\s*[\"']?[A-Za-z0-9_-]{6,}",
}
MENTIONS = {
    "bearer": rb"(?i)\bbearer\b",
    "private_key": rb"(?i)private[ _-]key",
    "claim_code": rb"(?i)claim[ _-]code",
    "api_key": rb"(?i)api[ _-]key",
    "token": rb"(?i)\b[a-z_]*token\b",
    "dot_env": rb"(?<![A-Za-z0-9_.])\.env\b",
}
SECRET_NAME = re.compile(r"(^|/)\.env(\.|$)|\.pem$|\.key$|id_rsa|id_ed25519|credentials", re.I)
PUBLIC_NAMES = re.compile(r"windows-ca-bundle\.pem$")
SHAPE_RES = {name: re.compile(p) for name, p in SHAPES.items()}
MENTION_RES = {name: re.compile(p) for name, p in MENTIONS.items()}


def lower_priority():
    """So a long scan does not slow the running kit. Windows only; elsewhere it does nothing."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except (ImportError, AttributeError, OSError):
        pass


def pem_values(path):
    """The PEM body as it would appear whole or line by line, and the raw key in the usual text forms."""
    text = path.read_text(encoding="utf-8")
    if "PRIVATE KEY" not in text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("-----")]
    values = lines + ["".join(lines)]
    try:
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(text.encode(), password=None)
        raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        values += [raw.hex(), base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode().rstrip("=")]
    except Exception:
        pass
    return values


def secret_fields(obj):
    """String values under secret-looking names, skipping names known to hold public data."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                if SECRET_FIELD.search(str(key)) and str(key) not in PUBLIC_FIELDS:
                    yield value
            else:
                yield from secret_fields(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from secret_fields(value)


def load_secrets(base=ROOT, environ=None):
    """{bytes value: label}. The labels name a source, never a value."""
    base = Path(base)
    environ = os.environ if environ is None else environ
    found = {}

    def add(value, label):
        value = str(value).strip().strip("'\"")
        if len(value) >= MIN_SECRET:
            found.setdefault(value.encode(), label)

    student = base.parent
    for env in [p for p in student.rglob(".env*") if ".venv" not in p.parts and "site-packages" not in p.parts and p.is_file()]:
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                name, value = line.split("=", 1)
                add(value, f"dotenv:{name.strip()}")
    for pem in [p for p in student.rglob("*.pem") if ".venv" not in p.parts and "site-packages" not in p.parts and not PUBLIC_NAMES.search(p.name)]:
        for value in pem_values(pem):
            add(value, f"pem:{pem.parent.name}/{pem.name}")
    for name, value in environ.items():
        if SECRET_ENV.search(name) and name not in SKIP_ENV:
            add(value, f"environment:{name}")
    market = base / ".market"
    if market.is_dir():
        for path in market.rglob("*"):
            if path.suffix == ".json" or path.suffix == ".jsonl":
                try:
                    text = path.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                rows = [text] if path.suffix == ".json" else text.splitlines()
                for row in rows:
                    try:
                        data = json.loads(row)
                    except ValueError:
                        continue
                    for value in secret_fields(data):
                        add(value, f"market:{path.name}")
    return found


def scan_line(raw, secret_re, secrets):
    """Kinds found in one line. UTF-16 logs from PowerShell become plain bytes first, or every pattern would miss them."""
    line = raw.replace(b"\0", b"")
    kinds = []
    if secret_re is not None:
        kinds += sorted({f"exact:{secrets[m.group()]}" for m in secret_re.finditer(line)})
    kinds += [f"shape:{name}" for name, pattern in SHAPE_RES.items() if pattern.search(line)]
    kinds += [f"mention:{name}" for name, pattern in MENTION_RES.items() if pattern.search(line)]
    return kinds


def masked(raw, width=48):
    """Context around each shape hit with the token gone, so a person can tell a placeholder from a value without seeing one."""
    line = raw.replace(b"\0", b"").decode("utf-8", "replace")
    views = []
    for name, pattern in SHAPE_RES.items():
        for match in pattern.finditer(raw.replace(b"\0", b"")):
            text = match.group().decode("utf-8", "replace")
            before = line[max(0, line.find(text) - width):line.find(text)]
            after = line[line.find(text) + len(text):][:width]
            scrub = lambda s: re.sub(r"[A-Za-z0-9_+/=-]{16,}", "<long>", s)
            views.append(f"{name}: ...{scrub(before)}<token len={len(text)}>{scrub(after)}...")
    return views


def run_git(args, cwd):
    return subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True, creationflags=LOW_PRIORITY, check=True).stdout


def added_lines(patch):
    """(path, line number, bytes) for each added line of a -U0 patch."""
    path, number, remaining = None, 0, 0
    for raw in patch.split(b"\n"):
        if remaining > 0:
            if raw.startswith(b"+"):
                yield path, number, raw[1:]
                number += 1
                remaining -= 1
            continue
        if raw.startswith(b"+++ "):
            name = raw[4:].decode("utf-8", "replace").strip()
            path = name[2:] if name.startswith("b/") else name
            continue
        hunk = HUNK.match(raw)
        if hunk:
            number = int(hunk.group(1))
            remaining = int(hunk.group(2) or 1)


def scan_commits(commits, cwd, secret_re, secrets, triage=None):
    hits, stats, names, binary = [], Counter(), [], set()
    for commit in commits:
        stats["commits"] += 1
        patch = run_git(["diff-tree", "-p", "-U0", "--text", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-commit-id", "--root", "-m", commit], cwd)
        seen_paths = set()
        for path, number, raw in added_lines(patch):
            seen_paths.add(path)
            stats["added_lines"] += 1
            stats["added_bytes"] += len(raw)
            for kind in scan_line(raw, secret_re, secrets):
                hits.append({"commit": commit[:12], "file": path, "line": number, "kind": kind})
                if triage is not None and kind.startswith("shape:"):
                    triage.append((commit[:12], path, number, masked(raw)))
        stats["paths"] += len(seen_paths)
        listing = run_git(["diff-tree", "--numstat", "--diff-filter=A", "--no-renames", "--no-commit-id", "--root", "-m", commit], cwd).decode("utf-8", "replace")
        for row in listing.splitlines():
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            if SECRET_NAME.search(parts[2]) and not PUBLIC_NAMES.search(parts[2]):
                names.append({"commit": commit[:12], "file": parts[2], "line": 0, "kind": "secret_file_name"})
            if parts[0] == "-":
                binary.add(parts[2])
    return hits + names, stats, binary


def scan_files(paths, secret_re, secrets, triage=None):
    hits, stats = [], Counter()
    for path in paths:
        stats["files"] += 1
        if SECRET_NAME.search(str(path).replace("\\", "/")) and not PUBLIC_NAMES.search(str(path)):
            hits.append({"commit": "worktree", "file": str(path), "line": 0, "kind": "secret_file_name"})
        with open(path, "rb") as handle:
            for number, raw in enumerate(handle, 1):
                stats["added_lines"] += 1
                stats["added_bytes"] += len(raw)
                for kind in scan_line(raw.rstrip(b"\r\n"), secret_re, secrets):
                    hits.append({"commit": "worktree", "file": str(path), "line": number, "kind": kind})
                    if triage is not None and kind.startswith("shape:"):
                        triage.append(("worktree", str(path), number, masked(raw.rstrip(b"\r\n"))))
    return hits, stats


def compile_secrets(secrets):
    return re.compile(b"|".join(re.escape(v) for v in sorted(secrets, key=len, reverse=True))) if secrets else None


def summary(hits):
    counts = Counter(hit["kind"] for hit in hits)
    return {"exact": {k: v for k, v in counts.items() if k.startswith("exact:")}, "shape": {k: v for k, v in counts.items() if k.startswith("shape:")},
            "mention": {k: v for k, v in counts.items() if k.startswith("mention:")}, "secret_file_name": counts.get("secret_file_name", 0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", help="a text file listing working-tree paths to scan instead of commits")
    parser.add_argument("--range", default="HEAD --not --remotes", help="arguments for git rev-list")
    parser.add_argument("--label", default="commits")
    parser.add_argument("--triage", action="store_true", help="print masked context for shape hits")
    args = parser.parse_args()
    lower_priority()
    secrets = load_secrets()
    secret_re = compile_secrets(secrets)
    triage = [] if args.triage else None
    started = datetime.now(timezone.utc)
    binary = set()
    if args.files:
        paths = [p for p in Path(args.files).read_text(encoding="utf-8").splitlines() if p.strip()]
        hits, stats = scan_files(paths, secret_re, secrets, triage)
        target = {"files_listed": args.files}
    else:
        commits = run_git(["rev-list", *args.range.split()], ROOT).decode().split()
        hits, stats, binary = scan_commits(commits, ROOT, secret_re, secrets, triage)
        target = {"rev_list": args.range, "head": run_git(["rev-parse", "HEAD"], ROOT).decode().strip()}
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    non_mentions = [h for h in hits if not h["kind"].startswith("mention:")]
    report = {"note": "commit, file, line and kind only; no values. exact = equals a real local secret, shape = looks like a token, mention = the word only.",
              "started": started.isoformat(), "target": target, "stats": dict(stats), "local_secrets_compared": len(secrets),
              "secret_sources": dict(Counter(label.split(":", 1)[0] for label in secrets.values())), "summary": summary(hits),
              "binary_files_added": sorted(binary), "hits": non_mentions, "mention_counts_by_file": dict(Counter(f"{h['file']}|{h['kind']}" for h in hits if h["kind"].startswith("mention:")).most_common(200))}
    out = OUT / f"scan-{args.label}-{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"report": str(out), "stats": dict(stats), "secrets_compared": len(secrets), "sources": report["secret_sources"], "summary": report["summary"], "binary": len(binary)}, ensure_ascii=False, indent=1))
    if triage:
        for commit, path, number, views in triage:
            sys.stdout.buffer.write(f"{commit} {path}:{number}\n".encode("utf-8", "replace"))
            for view in views:
                sys.stdout.buffer.write(("   " + view.replace("\n", " ") + "\n").encode("utf-8", "replace"))
    sys.stdout.flush()
    return 1 if non_mentions else 0


if __name__ == "__main__":
    sys.exit(main())
