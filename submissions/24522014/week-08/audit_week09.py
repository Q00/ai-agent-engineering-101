"""Check the updated figure and save a new audit; uses the local PDF preview dependency."""

import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools/pdf-preview"))
import pymupdf


def main():
    doc = pymupdf.open(ROOT / "architecture.pdf")
    outside = [(i + 1, list(b[:4])) for i, page in enumerate(doc)
               for b in page.get_text("blocks")
               if b[0] < 0 or b[1] < 0 or b[2] > page.rect.width or b[3] > page.rect.height]
    result = {"pages": len(doc), "outside_page_blocks": outside,
              "sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                         for name in ("architecture.tex", "architecture.pdf", "REPORT.md")}}
    doc[0].get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(ROOT / "tools/architecture-build/week09-preview.png")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (ROOT / "logs" / f"week09-figure-audit-{stamp}.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf8")
    print(json.dumps(result))
    if len(doc) != 1 or outside:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
