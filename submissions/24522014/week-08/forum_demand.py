"""Literal category/SKU matching. Forum text is data, never an instruction."""

import re

REQUEST = re.compile(r"구합니다|구매하고|찾습니다|\b(?:want(?:ed)?|need|looking for|buying)\b", re.I)


def match_request(text, catalog):
    text = str(text)[:2000]
    tokens = set(re.findall(r"[\w.-]+", text))
    exact = sorted(tokens & catalog.keys())
    if exact:
        return exact, "sku"
    # Prefer the most specific full path/leaf phrase over broad ancestor words.
    categories = {p.get("category", "") for p in catalog.values()} - {""}
    matches = []
    for category in categories:
        segments = category.split(">")
        for depth, segment in enumerate(segments):
            terms = [segment] + segment.split("/")
            for term in terms:
                if len(term) < 2 or term in {"기타", "상품", "식품", "가구", "용품", "생활"}:
                    continue
                if re.search(r"(?<![\w])" + re.escape(term) + r"(?=$|[^\w]|[을를은는이가도])", text, re.I):
                    matches.append((len(term), depth, category))
    if not matches:
        return [], "none"
    specificity = max((length, depth) for length, depth, _ in matches)
    selected = {category for length, depth, category in matches if (length, depth) == specificity}
    return sorted(sku for sku, product in catalog.items() if product.get("category") in selected), "category"


def requests(posts, catalog, now, max_age=7200):
    result = []
    for post in posts:
        if (not isinstance(post, dict) or post.get("round") != 3 or post.get("role") != "buyer"
                or post.get("author") == "kcwarni" or post.get("owner") == "kcwarni"
                or type(post.get("id")) is not int or type(post.get("ts")) not in (int, float)
                or not 0 <= now-post["ts"] <= max_age or not REQUEST.search(str(post.get("text", ""))[:2000])):
            continue
        skus, source = match_request(post.get("text", ""), catalog)
        if skus:
            result.append({"id": post["id"], "ts": post["ts"], "author": post.get("author"),
                           "skus": skus, "match": source})
    return sorted(result, key=lambda r: r["ts"], reverse=True)
