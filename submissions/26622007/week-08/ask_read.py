"""The shop's current ask read from its text by rule, or None for Jev / the harness.

A shop's text is read only when (1) one number stands after an ask word at the start of a sentence or field ("asking
12900", "Asking price: 5900", "Counteroffer: 13049", "Current ask is 8800", "ask=3564"), every such number is the same,
and the number ends its field (a full stop, the end, "(", "|", a new line); (2) every other number in the text is
explained by a field the shops write that is not a price: the product name before the first field, a SKU or unit id,
hours of shelf life or warranty, offers left, the list price, a share in %, my own offer named back ("11300 is too
low"); and (3) no word is left that can turn the ask into something else: was, now, today, for two, each, members,
coupon, a number in words, another SKU, ... Anything else and the text is not read: four independent reviews (11/07)
broke word lists that read free text ("Was asking 20000, now just 12000", "asking 19000 minus 2000", "Others ask 19000,
I accept your offer"), and a fifth (review/W-20261107-0840-AX호가정규식) the first version of this one ("asking 18000
for two", "Asking 9000. Correction: eleven thousand.", "Today only 7000. Grade A, asking 9000.").

Built on the shop texts in logs/market-kit.log and its kept parts (11/04-11/07), with the harness's reading as the
label (cases in testdata/ask_read_cases.jsonl).
"""
import re

import hard_limits

MAX_CHARS = 2000
NUM = r"(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]{0,11})"
# a number that is a price: not followed by a digit, a decimal, a letter, a unit, % or a money word
PRICE = (rf"(?P<p>{NUM})(?![0-9A-Za-z]|[.,][0-9])"
         r"(?![ \t]*(?:%|h\b|hours?\b|-hour|시간|units?\b|pcs\b|pieces?\b|개|병|팩|kg\b|g\b|ml\b|l\b|cm\b|mm\b|"
         r"k\b|thousand|million|usd|dollars?|cents?|won\b|만|천|백))")
CUR = r"(?:KRW[ \t]*|₩[ \t]*)?"
# the number ends its field: a full stop, ! ; | ( a new line or the end, or a field the shops write next
END = r"(?=[ \t]*(?:[.!;|(\n]|$|with Safe-Care \(|Counteroffer:|Send your offer|Offer a whole number|Remaining shelf life))"
# an ask word where a sentence or a field begins (the text's start, after . : ; ! ? | ) ] a dash or a new line, after a
# number field and its comma: "List price 19500, asking 15053"; "For the grade B, asking 7000" is not one)
START = r"(?:^|(?<=[.:;!?|)\]—\n])[ \t]*|(?<=[0-9], )|(?<=, and )|(?<=\bG[0-9a-f]{8} ))"
ASK_WORDS = (r"asking(?:[ \t]+price)?(?:[ \t]+is)?|current[ \t]+ask(?:[ \t]+is)?|counteroffer(?:[ \t]+price)?|"
             r"your[ \t]+price(?:[ \t]+is|:)|(?:the|our|my)[ \t]+(?:current[ \t]+|updated[ \t]+|final[ \t]+|new[ \t]+)?"
             r"(?:asking[ \t]+)?price[ \t]+is(?:[ \t]+now)?|(?:current|our[ \t]+new)[ \t]+price:|"
             r"here[ \t]+is[ \t]+our[ \t]+new[ \t]+price:|our[ \t]+offer[ \t]+is|our[ \t]+ask:?|"
             r"our[ \t]+lowest[ \t]+price[ \t]+for[ \t]+this[ \t]+unit|price:")
ANCHORS = [
    re.compile(START + rf"(?:{ASK_WORDS})[ \t]*[:=]?[ \t]*{CUR}{PRICE}(?:[ \t]*(?:KRW\b|원(?![가-힣])))?" + END, re.I),
    re.compile(rf"(?<=[ |])ask={PRICE}(?=[ \t]*[(|])"),
    # "19,500원에 제안드립니다." (the shop's own Korean line before its "Counteroffer: 19500.")
    re.compile(rf"(?:{START}|(?<=이며 )|(?<=이며, )){PRICE}원에 제안드립니다\."),
    # "..., and 568160 is our final price."
    re.compile(START + rf"{PRICE}[ \t]+is[ \t]+our[ \t]+(?:final[ \t]+|new[ \t]+|updated[ \t]+)?price\.", re.I),
    re.compile(START + rf"(?:[A-C]등급 (?:리퍼 )?(?:제품|상품)이며[ \t])?가격은 {PRICE}원입니다\."),
]
# "name: 12900 KRW, a premium unit ...": only where the text before the colon is a product name (_name_len)
NAME_KRW = re.compile(rf"(?P<name>[^:\n]{{1,200}}):[ \t]*{PRICE}[ \t]*KRW, a premium unit\b")
# fields that hold numbers but no price; each is cut out before the leftover numbers are counted
BENIGN = [
    r"\bG[0-9a-f]{8}\b", r"\bu-[0-9a-f]+\b", r"\breceipt=Ed25519\b", r"\bshop_fp=[0-9a-f]+\b",
    r"\b[0-9]{1,3}(?:\.[0-9]+)?[ \t]*(?:h\b|hours?\b|-hour\b|시간)",
    r"\bshelf_left=[0-9]+h\b",
    r"\b(?:about|at least) [0-9]+(?:\.[0-9]+)?[ \t]*(?:h\b|hours?\b)", r"\ba fair amount of shelf life left\b",
    r"\bmarket maximum\b", r"\bwith no defects\b", r"\bthe quote includes the warranty surcharge\b",
    r"(?<=shelf life left, )no warranty\b", r"(?<=grade [ABC], )no warranty\b", r"\bNo breakage warranty offered\b",
    r"\b(?:the )?box is damaged, the item is fine\b", r"\bbox damaged(?:, item fine)?\b",
    r"\ba lower offer ends this negotiation\b", r"\bdamaged packaging\b",
    r"\bwe(?:'ve| have)? (?:came|come) down(?: a (?:little|bit))?(?: on our side)?\b",
    r"\b(?:a |the )?damaged box\b", r"\bthe box is a little damaged\b", r"\bSend a higher offer as a number\b", r"\bfrom our (?:last offer|earlier asking price)(?=\.)",
    r"\bRefurbished unit sold as is, without a break warranty(?=\.)", r"\{\"buy\": \[\]\}$", r"\b(?:the full price |in full )?from our balance\b",
    r"\b(?:[0-9]+|one|two|three|four|five|six|seven|eight|nine|ten)[ \t]+offers?[ \t]+left\b",
    r"\bcloses after [0-9]+ offers\b",
    # durations written in words: "a three-day warranty", "six-hour warranty"
    r"\b(?:one|two|three|four|five|six|seven|twelve|twenty-four|forty-eight|seventy-two)[- ](?:day|hour|week)s?\b",
    rf"\blist_price={NUM}\b", rf"(?<=\| )list={NUM}\b", rf"(?:^|(?<=[ (,]))list price:?[ \t]*{NUM}\b",
    rf"\(\s*{NUM}\s+below catalog list\s+{NUM}\s*\)",
    r"\(-?[0-9]+(?:\.[0-9]+)?%[ \t]*vs list\)", rf"\([0-9]{{1,2}}% below list(?: price)? {NUM}\)",
]
BENIGN_RE = re.compile("|".join(BENIGN), re.I)
# my own offer named back: "11300 is too low", checked against my last offer by the caller
YOURS = re.compile(rf"(?:^|(?<=[.!][ \t]))(?P<y>{NUM}) is too low\.", re.I)
# "Opening quote discount included: 13500 -> 12900; saving 600": kept only when the arrow ends at the ask
DISCOUNT = re.compile(rf"Opening quote discount included:[ \t]*(?P<a>{NUM})[ \t]*->[ \t]*(?P<b>{NUM});[ \t]*saving[ \t]*"
                      rf"(?P<s>{NUM})\.", re.I)
NUMBER_WORDS = (r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
                r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
                r"million|dozen|couple|single|second")
# words that can make the ask something else
DANGER_EN = (r"\b(?:was|were|previous(?:ly)?|used to|no longer|now|not|never|instead|rather|minus|plus|less|"
             r"other|others|another|elsewhere|match|beat|if|unit price|each|per|both|pair|set of|total|half|double|"
             r"bundle|units|pieces|pcs|tax|vat|shipping|delivery|deposit|fee|"
             rf"{NUMBER_WORDS}|"
             r"today|tomorrow|yesterday|tonight|later|until|after|before|expires?|correction|typo|meant|kidding|"
             r"actually|discount|off|coupon|rebate|member|members|wholesale|student|students|credit|monthly|month|"
             r"year|weekly|week|upfront|down payment|balance (?:later|due)|installments?|sale|special|cheap|cheaper|alternative|"
             r"bigger|smaller|size|for (?:the )?grade|refurbished one|pickup|open-box|reserved|sold out|free|usd|dollars?|cents?|"
             r"percent|k|only|otherwise|except|offer valid|valid)\b|n't")
# Korean: a price in words (구천오백, 만칠천원), now, today, half price, members, a set, ... and any question
DANGER_KO = (r"[십백천만억]|지금|이제|오늘|내일|어제|반값|할인|원래|이전|기존|정가|대신|다른|개당|합계|총액|배송비|부가세|추가|"
             r"빼|깎|회원|세트|두 ?개|한 ?개|쿠폰|적립|특가|만원|\?")
DANGER = re.compile(DANGER_EN + "|" + DANGER_KO, re.I)
# a product name may say 개당, 세트, 천연, 한정특가 or 2개 about the goods; nothing else on the list
NAME_DANGER = re.compile(DANGER_EN + r"|\b(?:[0-9]+-?pack|x[0-9]|rental|lease|rent|empty|box only)\b|렌탈|대여|보증|지금|이제|오늘|내일|어제|반값|할인|원래|이전|기존|정가|대신|다른|만원|쿠폰|"
                         r"[일이삼사오육칠팔구]?[십백]?[천만]원?에|\?", re.I)
# sentences the shops write that hold a danger word or a number but do not change the ask; cut before anything is read
SAFE_SENTENCES = [
    r"\(not a long-term guarantee\)",
    r"condition can change while negotiating\.?",
    r"Lower-priced alternative: (?P<altsku>G[0-9a-f]{8}) (?P<alt>(?:[^.\n]|\.(?=\d)){1,120})\. Request a separate quote for that SKU; this quote remains for "
    r"(?P<keep>G[0-9a-f]{8})\.",
    r"We also have a cheaper (?:refurbished \(grade C\)|box-damaged \(grade B, the item is fine\)) one if you prefer\.",
    r"\bif (?:it|the (?:item|unit|goods)) breaks within [0-9]+ h of the sale\b",
    r"If you can go a little higher, we'd be glad to (?:review it|take another look)\.",
    r"If you'd like to make another offer, send it as a number\.",
    r"Feel free to send us a better offer as a number\.",
    # goods are never bought without a warranty: that option's price is not the ask
    r"No-warranty option: " + NUM + r" credits\. Other warranty durations are optional\.",
    r"or reply 'accept' to buy now\.",
    r"A separate no-warranty quote is available; its total is confirmed in that quote\.",
    r"Warranty options \(base price surcharge\):(?: [0-9]{1,3}h \+[0-9]{1,3}%,?)+\.",
    r"Choose in a new quote; funding approval required\.",
    r"Add another distinct item: verified purchases within [0-9]+ minutes unlock(?: [0-9]%,)+ up to [0-9]% off the next "
    r"quote, subject to its minimum price\.",
    r"Each item needs a separate market order\.",
    r"Send prior paid receipt task IDs for a discount quote\.",
    r"Warranty [0-9]+ h included in this total price; covered breakage receives a full market refund\.",
    r"Negotiable; no extra automatic discount\.",
    r"we can do better if you raise your offer\.",
    r"Send one price as a number(?:, or accept)?",
    r"One unit at the",
    r"Break warranty included at no extra charge: goods can only break in the first hours after a sale, and if it "
    r"breaks the market refunds you in full from our balance\.",
    r"Break warranty included at no extra charge: goods can only break in the first",
    r"\bif (?:it|the (?:unit|goods|item|mattress|sofa|bed|set|product)) breaks(?: (?:with)?in (?:that|the) (?:time|period))?",
    r"if the goods break during warranty",
    r"a full refund from our balance if it breaks within that time",
    # a question that asks for my offer and names no number
    r"(?<![^\s.!?:;])(?:What is|Could you send me|Would you send|Please tell me) your offer(?: as a number)?\?",
    r"Bought here before\? Send your receipt codes for a loyalty discount\.",
    # an option without the warranty: goods are never bought without one, so its price is not the ask
    r"Without Safe-Care: " + NUM + r" \(no refund\); say 'without care' to take that option\.",
]
SAFE_RE = re.compile("|".join(SAFE_SENTENCES), re.I)
# the shop names its ask again as the one-word yes: kept only when it is the ask
ACCEPTS = re.compile(rf"Reply with an offer or accept (?P<acc>{NUM}) to close now\.|"
                     rf"or reply (?P<acc2>{NUM}) for an instant Ed25519-signed receipt\.", re.I)
# a line some shops open with before the name
PREFIX = re.compile(r"Inventory condition: [ABC]\. Confirm the certificate at checkout; condition can change while "
                    r"negotiating\.(?: Warranty: [0-9]+ hours\.)?\n")
# the product name: the text up to the first field it opens with
NAME_END = re.compile(r"[ \t]\((?:G[0-9a-f]{8}|sku)|:[ \t]|[ \t]\||\.[ \t]+Grade\b|\(G[0-9a-f]{8}\)")

# every word left once the fields are cut must be one the shops in the log write around an ask (11/04-11/07): a word
# outside it ("apiece", "OBO", "fees", "deposit", "lease", "estimated", ...) and the text goes to the harness. Left out on
# purpose although logged: without, sold (as is), from, last, earlier, about, least, amount, maximum, set, and no, lower,
# higher, surcharge, damaged outside the logged phrases in BENIGN (second review F5)
VOCAB = frozenset([
    'packaging', 'a등급', 'b등급', 'c등급', 'a등급이며', 'b등급이며', 'c등급이며', 'b등급으로', '품절', '전에',
    'a', 'accept', 'again', 'also', 'and', 'any', 'appreciate', 'as', 'ask', 'asked', 'asking', 'at', 'attached',
    'b', 'beautyrest', 'bed', 'box', 'brand', 'break', 'breakage', 'breakdown', 'but', 'buy', 'c',
    'c-grade', 'came', 'carries', 'cert', 'certificate', 'check', 'checkout', 'close', 'come', 'comes', 'condition',
    'covering', 'covers', 'data', 'date', 'defects', 'ends', 'fair', 'final', 'fine', 'for',
    'fresh', 'full', 'full-refund', 'fully', 'grade', 'guarantee', 'has', 'have', 'in', 'included',
    'includes', 'including', 'interest', 'is', 'it', 'item', 'its', 'itself', 'kimchi', 'left', 'life', 'little',
    'lowered', 'market', "market's", 'market-enforced', 'market-signed', 'mattress', 'me', 'near', 'need',
    'negotiation', 'new', 'normal', 'number', 'of', 'offer', 'offered', 'ok', 'on', 'or', 'our', 'part',
    'perfect', 'period', 'periods', 'please', 'plenty', 'price', 'quote', 'quoted', 'radish', 'refund', 'refunded',
    'refunds', 'refurbished', 'replies', 'reply', 'request', 'requests', 'requirement', 'review', 'safe-care',
    'save', 'say', 'send', 'shelf', 'short', 'shorter', 'shown', 'side', 'simmons', 'sku', 'sns', 'so', 'sofa',
    'still', 'stock', 'sweet', 'take', 'tell', 'terms', 'thank', 'thanks', 'that', 'the', 'this',
    'time', 'to', 'tokens', 'too', 'true', 'unit', 'warranty', 'we', "we'll", "we've", 'what', 'where', 'which',
    'while', 'whole', 'whose', 'with', 'within', 'you', 'young', 'your', '감사합니다', '광고했다는', '그', '김치는', '난', '남아',
    '남았습니다', '남은', '대통령도', '데려가세요', '등급', '등급으로', '등급이며', '리퍼', '바로', '박스가', '보증', '보증을', '보증의', '뷰티레스트', '사과이며',
    '사이에서', '상태입니다', '상품으로', '상품은', '상품이며', '상품입니다', '스위트', '스타들이', '시몬스', '시장', '쓰고', '쓴다는', '안심케어', '않았습니다', '약',
    '여부는', '영수증', '영수증에서', '영화배우가', '올린', '요즘', '요청하신', '유명', '유통기한까지', '유통기한은', '유통기한이', '유통시간은', '을', '이상을',
    '인플루언서들', '입니다', '입소문', '있습니다', '있어', '적용', '정상', '제공합니다', '제안', '제품', '종가', '주세요', '직접', '챙겨', '총각김치', '촬영장에서도',
    '최대', '충족합니다', '트럼프', '해당', '허용', '현재', '화제의', '확인되지', '확인해', '후기', '훼손된',
])
WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")
# the marks left between those words; any other (+ ~ x % $ = after the cut, braces) and the text goes to the harness
MARKS = set(".,:;()'-!/|=")

# goods that come without the warranty (or a 0 h one): the item can break worth 0, so the harness prices the turn (second
# review F1); the option sentences that offer it beside the warranted ask are cut first (SAFE_SENTENCES)
NO_WARRANTY = re.compile(r"\bno[ -]?(?:breakage |break )?(?:warranty|refund|safe-care)\b|"
                         r"\bwithout (?:a |any )?(?:break |breakage )?(?:warranty|safe-care|refund)\b|\bas[- ]is\b|"
                         r"\bwarranty(?: period)?[ \t]*(?::|=|of|is)?[ \t]*0(?:\.0+)?[ \t]*(?:h\b|hours?\b)|"
                         r"(?<![\w.])0(?:\.0+)?[ \t-]*(?:h|hours?|hour|시간)\b[ \t-]+(?:[a-z-]+ ){0,2}warranty\b|"
                         r"safe-care \(0\b|보증[ \t]*0|보증[ \t]*(?:없|미제공)|무보증", re.I)
# the warranty hours the text names ("6-hour full-refund warranty", "warranty: 24 h", "three-day warranty")
WARRANTY_HOURS = re.compile(r"(?<![\w.])(?P<n>[0-9]{1,4}(?:\.[0-9]+)?|one|two|three|four|five|six|seven|twelve|twenty-four|"
                            r"forty-eight|seventy-two)[ \t-]*(?P<u>h|hours?|hour|day|days|시간)\b[ \t-]+"
                            r"(?:[a-z-]+ ){0,2}warranty\b|\bwarranty(?: period)?[ \t]*(?::|=|of|is)?[ \t]*"
                            r"(?P<n2>[0-9]{1,4}(?:\.[0-9]+)?)[ \t]*(?P<u2>h|hours?|days?)\b", re.I)
WORD_N = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "twelve": 12, "twenty-four": 24,
          "forty-eight": 48, "seventy-two": 72}
GOODS_WARRANTY_HOURS = hard_limits.BUYER_GOODS_WARRANTY_HOURS     # the kit pays for goods only with that much


def no_warranty(text):
    """True when goods text says the unit comes without a warranty, with a 0 h one, or names only warranties shorter
    than the 6 h the kit pays for (third check: "0-hour warranty", "three-hour full-refund warranty")."""
    t = SAFE_RE.sub(_keep_stop, text or "")
    if NO_WARRANTY.search(t):
        return True
    hours = []
    for m in WARRANTY_HOURS.finditer(t):
        n, u = (m["n"], m["u"]) if m["n"] else (m["n2"], m["u2"])
        v = WORD_N.get(n.lower()) if n.lower() in WORD_N else float(n)
        hours.append(v * 24 if u.lower().startswith("day") else v)
    return bool(hours) and max(hours) < GOODS_WARRANTY_HOURS


def short_warranty_hours(text):
    """The hours of a short warranty in goods text: 0 when it says none or 0 h anywhere (even beside a longer one, the
    stricter reading), else the largest named when under 6 h; None when it names nothing short (buyer_short_warranty
    prices the deal at those hours)."""
    t = SAFE_RE.sub(_keep_stop, text or "")
    hours = []
    for m in WARRANTY_HOURS.finditer(t):
        n, u = (m["n"], m["u"]) if m["n"] else (m["n2"], m["u2"])
        v = WORD_N.get(n.lower()) if n.lower() in WORD_N else float(n)
        hours.append(v * 24 if u.lower().startswith("day") else v)
    if NO_WARRANTY.search(t):
        return 0.0
    return float(max(hours)) if hours and max(hours) < GOODS_WARRANTY_HOURS else None


def _alt_bad(name):
    """The alternative's name holds a clause (; :), a SKU id, or a number of 3+ digits with no unit after it."""
    return bool(re.search(r"[;:]|\bG[0-9a-f]{8}\b|(?<![\d.])[0-9]{3,}(?![0-9.]*(?:[a-z가-힣×]))", name, re.I))


def _name_ok(name):
    name = name.replace("price capped by grade", "")       # "(grade C, three-hour full-refund warranty, price capped by grade)"
    return not (re.search(r"\bpack\b|\bsubscription\b|\btrial\b|\bsample\b", name, re.I) or NAME_DANGER.search(name) or any(rx.search(name) for rx in ANCHORS)
                or re.search(r"\b(?:asking|ask|counteroffer|lowest|current|price)\b|\d[ \t]*(?:KRW|원|won)\b|[₩$]",
                             name, re.I))


def _name_len(text):
    """How many leading characters are the product name (0 when the text opens with a field, an ask word or a word
    that can change a price)."""
    head = text[:200]
    m = re.match(r"(?:안녕하세요, )?요청하신 [^.\n]{1,80}?입니다\.", head)      # "요청하신 아이시스 2L 6개입니다."
    if m:
        return m.end() if _name_ok(m[0]) and "원" not in m[0] else 0
    if head.startswith("[sku "):
        m = re.match(r"\[sku G[0-9a-f]{8}\] [^|\n]{1,160}\|", head)
        return m.end() if m and _name_ok(m[0][15:]) else 0
    start = len("Special concession — ") if head.startswith("Special concession — ") else 0
    e = NAME_END.search(head, start)
    if not e:
        return 0
    return e.start() if _name_ok(head[start:e.start()]) else 0


def _keep_stop(m):
    s = m[0]
    return " " * (len(s) - 1) + s[-1] if s[-1] in ".?)" else " " * len(s)


def _blank(text, spans):
    chars = list(text)
    for a, b in spans:
        chars[a:b] = " " * (b - a)
    return "".join(chars)


def read(text):
    """(ask, yours): the ask the shop's text names, and the numbers it names as my offer ("N is too low"), or
    (None, ()) when the text is not read by rule."""
    if not text or len(text) > MAX_CHARS:
        return None, ()
    # the yes-lines must name the ask; the safe sentences are cut before any ask is looked for, so an ask inside one
    # (another SKU's price in its "Lower-priced alternative") is never read
    accepts = {int((m["acc"] or m["acc2"]).replace(",", "")) for m in ACCEPTS.finditer(text)}
    safe = list(SAFE_RE.finditer(text))
    keep = {m["keep"] for m in safe if m["keep"]}
    # the other SKU's name only: a sentence or a word that changes my ask is not hidden in it
    if any(m["alt"] and (not _name_ok(m["alt"]) or m["altsku"] == m["keep"] or _alt_bad(m["alt"])) for m in safe):
        return None, ()
    p = PREFIX.match(text)
    p = p.end() if p else 0
    # a cut sentence keeps its last stop, so the sentence after it still starts there
    t = SAFE_RE.sub(_keep_stop, ACCEPTS.sub(_keep_stop, text))
    n = p + _name_len(t[p:])
    asks = {int(m["p"].replace(",", "")) for rx in ANCHORS for m in rx.finditer(t, n)}
    k = NAME_KRW.match(t, p)
    if k and n == p + len(k["name"]):
        asks.add(int(k["p"].replace(",", "")))
    if len(asks) != 1:
        return None, ()
    ask = asks.pop()
    if accepts - {ask}:
        return None, ()
    if any(int(m["b"].replace(",", "")) != ask or int(m["a"].replace(",", "")) - ask != int(m["s"].replace(",", ""))
           for m in DISCOUNT.finditer(t)):
        return None, ()
    # one SKU only: a text that names another one may price that one
    if len(set(re.findall(r"\bG[0-9a-f]{8}\b", t[n:])) | keep) > 1:
        return None, ()
    yours = tuple(int(m["y"].replace(",", "")) for m in YOURS.finditer(t, n))
    spans = [(0, n)] + [m.span() for rx in [DISCOUNT, YOURS, *ANCHORS, BENIGN_RE] for m in rx.finditer(t, n)]
    if k and n == p + len(k["name"]):
        spans.append(k.span())
    cut = _blank(t, spans)
    if DANGER.search(cut):
        return None, ()
    # grades, list fields and the like are cut; any digit left (any script) is a number I cannot place
    if re.search(r"\d", cut):
        return None, ()
    if any(w.lower() not in VOCAB for w in WORD.findall(cut)) or set(re.findall(r"[^\w\s]", cut)) - MARKS:
        return None, ()
    return ask, yours
