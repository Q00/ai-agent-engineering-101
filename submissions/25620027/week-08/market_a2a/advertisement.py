"""Serve the static public shop explanation without external page dependencies."""

from pathlib import Path

from fastapi.responses import HTMLResponse

from market_a2a.round_mode import margin_mode


def advertisement() -> HTMLResponse:
    """Return the local advertisement independently of the process working folder."""
    filename = "seller-r3-ad.html" if margin_mode() else "seller-ad.html"
    page = Path(__file__).resolve().parents[1] / "assets" / filename
    return HTMLResponse(page.read_text(encoding="utf-8"))
