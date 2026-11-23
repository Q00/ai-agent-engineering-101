"""Install a reversible Groq 429 guard into Week 8 without touching keys.

Usage:
    python install_groq_guard.py --enable
    python install_groq_guard.py --check
    python install_groq_guard.py --disable
"""
from pathlib import Path
import argparse
import re
import shutil

TARGETS = ("harness.py", "buyer_adaptive.py")
LINE = "from groq_quota_guard import decide  # Week8 shared quota guard"


def patch(directory: Path, enable: bool):
    texts = {}
    for filename in TARGETS:
        path = directory / filename
        source = path.read_text(encoding="utf-8")
        if enable:
            if LINE in source:
                updated = source
            else:
                anchors = list(re.finditer(r"(?m)^from llm_harness import [^\n]*\bdecide\b[^\n]*$", source))
                if len(anchors) != 1:
                    raise RuntimeError(f"Expected exactly one llm_harness decision import in {filename}; no changes made")
                pos = anchors[0].end()
                updated = source[:pos] + "\n" + LINE + source[pos:]
        else:
            updated = source.replace("\n" + LINE, "")
        texts[path] = (source, updated)

    backups = Path.home() / "week08-groq-guard-backup"
    for path, (old, new) in texts.items():
        if old == new:
            continue
        backups.mkdir(parents=True, exist_ok=True)
        bak = backups / path.name
        if not bak.exists():
            shutil.copy2(path, bak)
        path.write_text(new, encoding="utf-8")
        print(f"Updated {path.name}")
    print("Guard", "ENABLED" if enable else "DISABLED", "(original sources backed up outside Git)")


def main():
    ap = argparse.ArgumentParser()
    op = ap.add_mutually_exclusive_group(required=True)
    op.add_argument("--enable", action="store_true")
    op.add_argument("--disable", action="store_true")
    op.add_argument("--check", action="store_true")
    args = ap.parse_args()
    directory = Path(__file__).resolve().parent
    if args.check:
        for filename in TARGETS:
            source = (directory / filename).read_text(encoding="utf-8")
            print(filename, "ENABLED" if LINE in source else "NOT ENABLED")
        return
    patch(directory, enable=args.enable)


if __name__ == "__main__":
    main()
