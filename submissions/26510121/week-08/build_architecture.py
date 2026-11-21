"""Build the one-page TikZ submission figure and retain each build console."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys

import pymupdf as fitz

ROOT = Path(__file__).resolve().parent


def main():
    build = ROOT / "artifacts" / "architecture-build"
    build.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log = ROOT / "logs" / f"architecture-build-{stamp}.txt"
    command = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
               "-output-directory=" + str(build), str(ROOT / "architecture.tex")]
    result = subprocess.run(command, cwd=ROOT, capture_output=True)
    console = result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace")
    with log.open("x", encoding="utf-8", newline="") as stream:
        stream.write("Command: " + repr(command) + "\n" + console)
    print("Build console:", log)
    if result.returncode:
        print(console[-5000:])
        return result.returncode
    doc = fitz.open(build / "architecture.pdf")
    print("Pages:", len(doc))
    print("TeX box warnings:", console.count("Overfull") + console.count("Underfull"))
    if len(doc) != 1:
        print("Expected one page; final PDF not replaced.")
        return 1
    page = doc[0]
    page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(build / "architecture-preview.png")
    print("Rendered:", build / "architecture-preview.png")
    doc.close()
    shutil.copyfile(build / "architecture.pdf", ROOT / "architecture.pdf")
    print("PDF:", ROOT / "architecture.pdf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
