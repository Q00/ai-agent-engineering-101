"""The buyer's market-reference line read by code instead of Jev (지시23, 11/10).

One buyer sends, word for word: "That is more than I want to pay. This item sold for <market price> on the market in
the last few hours. I can do <offer>." Two numbers, so fast_classify.bare_offer cannot take it and every such turn
called Jev (seller-jev). market-kit.log(.1) 11/10 08:48-13:59: 85 such turns, all this one shape; Jev read the "I can do"
number every time and the jevrule engine countered it (testdata/market_ref_cases.jsonl). They were 13 of the 16 Jev
calls in 12:25-12:55.

When the switch is on, seller_reader_trial.Reader answers that exact line with (offer, 1.0) and makes no Jev call. Every
check after the reader is unchanged (the offer must be written in the message, not one of our asks, under our ask, our
ask only with words of agreement, the SUSPECT screen, the price guards in seller.py), so the turn ends as it did when
Jev read the same number. Any other wording, an extra word or a second line still goes to Jev. One difference by design:
a Jev timeout on this line no longer holds the ask (negotiate HOLD_TURN), so the talk's once-a-talk hold stays available.

Only where Jev would read: with no Jev (FAST_JEV=0, another backend) the line goes to the harness as before, even in
the reader trial's arm B. The reader trial (seller_reader_trial.json) reads it by code in both arms while this is on.
Every trial whose trace records reader.source / jev_calls (reader, concession, late concession, opening cue, late speed,
jev_transport) then sees source "rule_market_ref" and jev_calls 0 on these turns: do not compare Jev latency or call
counts across the switch.
seller.py logs " [market-ref line read by code, no Jev call]" after the FAST reason of such a turn.

Switch: .market/market_ref_read.json (MARKET_DIR) {"enabled": true, "start_at": <epoch>, "stop_at": <epoch, optional>},
read at every such turn; absent, invalid, not a regular file, larger than 4 KiB, before start_at or at/after stop_at:
off (Jev reads as before). meta_harness.SWITCHES watches it; sol_seller_margin.SOURCE_FILES hashes this file.
"""
import json
import math
import os
from pathlib import Path
import re
import stat
import time

N = r"([1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)"
LINE = re.compile(r"That is more than I want to pay\. This item sold for " + N +
                  r" on the market in the last few hours\. I can do " + N + r"\.")
MAX_TEXT = 500
MAX_CONFIG_BYTES = 4096
KEYS = {"enabled", "start_at", "stop_at"}


def offer(text):
    """The "I can do" number of the exact market-reference line, else None."""
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        return None
    m = LINE.fullmatch(text.strip())
    return int(m[2].replace(",", "")) if m else None


def _time(v):
    return type(v) in (int, float) and math.isfinite(v)


class Switch:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(os.environ.get("MARKET_DIR") or Path(__file__).parent / ".market")

    def on(self, now=None):
        now = time.time() if now is None else now
        try:   # non-blocking open, then fstat: a FIFO swapped in never holds the turn; never more than the cap read
            fd = os.open(self.root / "market_ref_read.json", os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            return False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            data = os.read(fd, MAX_CONFIG_BYTES + 1)
            if len(data) > MAX_CONFIG_BYTES:
                return False
            c = json.loads(data.decode("utf-8"))
        except Exception:  # noqa: BLE001 - unreadable: off
            return False
        finally:
            os.close(fd)
        if not isinstance(c, dict) or set(c) - KEYS or c.get("enabled") is not True or not _time(c.get("start_at")):
            return False
        if "stop_at" in c and not _time(c["stop_at"]):
            return False
        return c["start_at"] <= now and ("stop_at" not in c or now < c["stop_at"])


def log_note(reader):
    """What seller.py adds to a FAST line when this turn's offer was read by code here."""
    return " [market-ref line read by code, no Jev call]" if getattr(reader, "source", None) == "rule_market_ref" else ""


SWITCH = Switch()
