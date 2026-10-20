"""Serve the static public shop explanation without external page dependencies."""

from pathlib import Path

from fastapi.responses import HTMLResponse


def advertisement() -> HTMLResponse:
    """Return the local advertisement independently of the process working folder."""
    page = Path(__file__).resolve().parents[1] / "assets" / "seller-ad.html"
    return HTMLResponse(page.read_text(encoding="utf-8"))
