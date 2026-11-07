"""Challenge-1 requests: what the buyer says, and the check a correct item must pass.

The buyer never names the item. Each check is a plain predicate over the item the
seller picked (the receipt's sku), so grading is mechanical.
"""
WOODS = {"oak", "pine", "walnut", "birch", "bamboo"}
has = lambda it, word: any(word in f.lower() for f in it["features"])

REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk",
     lambda it: it["category"] == "bookshelf" and it["material"] in WOODS and (it["capacity"] or 0) >= 35
     and it["height_cm"] <= 80),
    ("a dimmable reading light for my desk, made of metal",
     lambda it: it["category"] == "desk lamp" and it["material"] == "metal" and has(it, "dimmable")),
    ("a chair I can sit in all day at the computer, it must support my lower back",
     lambda it: it["category"] == "office chair" and has(it, "lumbar")),
    ("a wool rug for a small bedroom, about 120 by 170 cm",
     lambda it: it["category"] == "rug" and it["material"] == "wool" and it["size"] == "120x170"),
    ("somewhere by the front door to keep around 20 pairs of shoes",
     lambda it: it["category"] == "shoe cabinet" and (it["capacity"] or 0) >= 16),
    ("a desk I can raise so I can work standing up",
     lambda it: it["category"] == "desk" and has(it, "height adjustable")),
    ("a sofa for three people, but not leather because I have cats",
     lambda it: it["category"] == "sofa" and (it["capacity"] or 0) >= 3 and it["material"] != "leather"),
    ("a tall wooden lamp to light a corner of my living room",
     lambda it: it["category"] == "floor lamp" and it["material"] in WOODS),
]
