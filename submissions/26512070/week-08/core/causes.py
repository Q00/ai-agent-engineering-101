"""Why a call or a trade failed (design 16-1 C7, D-23): our limit, the counterpart, the network, our own error, or
the negotiation. Shared by tools.registry (each tool result) and harness.trajectory (each trade)."""
import re

# Checked in order; the first match wins.
CAUSES = [
    ("limit", r"^no deal: shelf|not paying: [\d.]+ h of shelf|no shop that is not blocked|guard|refused by check|"
              r"cash floor|stock cap|per-SKU|over the limit|below cost|under the floor"),
    ("counterpart", r"sig_seller|cheated|no certificate|checkout refused: 409|false grade|not an SKU"),
    ("network", r"could not reach|ReadTimeout|ConnectError|ConnectTimeout|RemoteProtocolError"),
    ("ours", r"RuntimeError|JSONDecodeError|tool error|Traceback|KeyError|TypeError|bad input"),
    ("negotiation", r"rejected|walked|canceled|cancelled"),
]


def cause(text):
    """limit / counterpart / network / ours / negotiation / other for a failure text, None for no text."""
    if not text:
        return None
    for name, pat in CAUSES:
        if re.search(pat, str(text)):
            return name
    return "other"
