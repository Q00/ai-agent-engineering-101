"""Challenge-2 shopping list: what the buyer is told to buy, and the check a bought item must pass."""
from ch1_requests import WOODS, has

LIST = [
    ("a dimmable desk lamp",
     lambda it: it["category"] == "desk lamp" and has(it, "dimmable")),
    ("a wooden bookshelf for about 60 books",
     lambda it: it["category"] == "bookshelf" and it["material"] in WOODS and (it["capacity"] or 0) >= 50),
    ("an office chair with lumbar support",
     lambda it: it["category"] == "office chair" and has(it, "lumbar")),
    ("a rug about 140 by 200 cm",
     lambda it: it["category"] == "rug" and it["size"] == "140x200"),
    ("a shoe cabinet for at least 20 pairs of shoes",
     lambda it: it["category"] == "shoe cabinet" and (it["capacity"] or 0) >= 20),
]
