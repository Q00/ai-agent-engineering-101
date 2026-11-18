"""The receipt amount checked by code before it is signed (person's decision 11/08 ~11:20).

The seller reads the buyer's offer as the first number of the message (seller_r3.py). When a buyer names other
numbers first ("inextro quoted me 76289 for this. I can do 64117."), that is not the buyer's offer, and a receipt
for it does not match what the buyer agreed to (one buyer's shop name was a number: a receipt of 25,620,027 for a
10,082 ask). agreed() gives the price the buyer agreed to:
  1. the number after an offer phrase (I can do / I offer / I'll pay / my offer / how about ...), the last one;
     numbers after "quoted me" and SKUs (G + 8 hex) never count; else the first other number (as the seller reads it)
  2. an offer over OVER_ASK x my last ask is taken as a typo (a buyer wrote 420673 to a 42835 ask): my last ask
The seller then issues the receipt at the agreed price (decision receipt_fixed), or counters when the agreed
price is under the floor. Pricing rules are not changed.
"""
import re
import time
from datetime import datetime

OVER_ASK = 2.0
SKU = re.compile(r"G[0-9a-f]{8}")
QUOTED = re.compile(r"quoted (?:me|us)?\s*(?:krw|₩)?\s*\d[\d,]*", re.I)
OFFER = re.compile(r"(?:i can do|i can pay|i(?:'ll| will)? (?:offer|pay)|my (?:final |best |new )?offer(?: is)?|"
                   r"how about|i(?:'d| would) (?:do|pay)|let'?s (?:say|do)|deal at|meet (?:at|you at))\s*:?\s*"
                   r"(?:krw|₩)?\s*(\d[\d,]*)", re.I)
NUM = re.compile(r"(?<![\w.])\d[\d,]*(?![A-Za-z0-9_.,%])(?!\s*(?:hours?\b|hrs?\b|h\b|units?\b|pcs\b|pieces\b|days?\b|minutes?\b|mins?\b|%|시간|개|일|분))", re.I)


UNITS = (r"(?!\s*(?:hours?|hrs?|h|units?|pcs|pieces|days?|minutes?|mins?|items?|packs?|kg|g|ml|l|cm|x)\b)"
         r"(?!\s*(?:%|시간|개|팩|박스|병|봉|kg))")   # not 입 / 일 / 분: "6732입니다", "...일까요" are prices
FIRST = re.compile(r"(?<![\d,.])(?:\d{1,3}(?:,\d{3})+|\d+)(?!\d)(?![.,]\d)" + UNITS, re.I)
FIRST_COMMA = re.compile(r"\d{1,3}(?:,\d{3})+(?!\d)|\d+")       # thousands only (from the 14:06 restart)
# person 11/08 ~13:30: the unit skipping goes with the seller restart after the 21:00 substitute experiment ends
UNITS_FROM = datetime(2026, 11, 8, 21, 5).timestamp()


def first_number(text, now=None):
    """The first number of a message that is a price: thousands separators read (11/08: "27,000" is 27000, it was
    read as 27), and from UNITS_FROM a number with a unit after it is skipped (11/08: "540 hours", "1 unit",
    "360.5 hours", "20%" were read as offers); the seller's offer parse (SKUs stripped before)."""
    rx = FIRST if (now or time.time()) >= UNITS_FROM else FIRST_COMMA
    m = rx.search(text or "")
    return int(m.group().replace(",", "")) if m else None


def _int(s):
    s = s.replace(",", "")
    return int(s) if s.isdigit() else None


def buyer_offer(text):
    """The buyer's own offer in one message, or None."""
    t = QUOTED.sub(" ", SKU.sub(" ", text or ""))
    hits = [_int(m.group(1)) for m in OFFER.finditer(t)]
    hits = [h for h in hits if h]
    if hits:
        return hits[-1]
    nums = [_int(m.group()) for m in NUM.finditer(t)]
    nums = [n for n in nums if n]
    return nums[0] if nums else None


def agreed(text, parsed, last_ask):
    """(agreed price, reason or None). reason is None when the parsed offer already is the agreed price."""
    own = buyer_offer(text)
    price, why = parsed, None
    if own is not None and parsed is not None and own != parsed:
        price, why = own, f"buyer's own offer {own:,} (the message's first number {parsed:,} was not it)"
    if last_ask and price is not None and price > OVER_ASK * last_ask:
        why = (f"offer {price:,} over {OVER_ASK:g} x my last ask {last_ask:,}: taken as a typo, my last ask"
               + (f"; {why}" if why else ""))
        price = last_ask
    return price, why
