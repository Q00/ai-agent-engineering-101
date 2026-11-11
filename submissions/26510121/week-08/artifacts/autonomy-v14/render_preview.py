"""Render the compiled diagram and check its page and text content."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pypdfium2 as pdfium


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=Path(__file__).with_name("framework.pdf"))
    parser.add_argument("--png", type=Path, default=Path(__file__).with_name("framework.png"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    with pdfium.PdfDocument(str(args.pdf)) as document:
        if len(document) != 1:
            raise ValueError(f"Expected one diagram page, got {len(document)}")
        page = document[0]
        width, height = page.get_size()
        text_page = page.get_textpage()
        text = text_page.get_text_range()
        required = [
            "Shop Main", "Buyer Main", "Director", "Need Assessor", "Skill Router",
            "Builder", "Evaluator", "Release Controller", "Context Curator",
            "Long Memory", "미구현", "미적용", "실거래 적용 금지",
        ]
        missing = [label for label in required if label not in text]
        if missing:
            raise ValueError(f"Missing diagram labels: {missing}")
        if abs(width - 1190.55) > 2 or abs(height - 841.89) > 2:
            raise ValueError(f"Expected landscape A3, got {width} x {height} pt")
        bitmap = page.render(scale=2.0)
        preview = bitmap.to_pil()
        preview.save(args.png)
        report = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "pdf": str(args.pdf),
            "pdf_sha256": hashlib.sha256(args.pdf.read_bytes()).hexdigest(),
            "pages": len(document),
            "page_size_pt": [width, height],
            "preview_pixels": list(preview.size),
            "required_labels_present": required,
            "scope": "Artifact render and text checks only; not framework validation or deployment.",
        }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
