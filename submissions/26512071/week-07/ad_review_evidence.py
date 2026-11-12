"""Challenge 2, the other side: buyers whose reviews advertise another shop.

Usage: python ad_review_evidence.py   (writes logs/challenge-2/ad-reviews-<time>.json and ad-reviews.md)

On 2026-10-21 17:25 the buyer cheapest-in-town (@danayoou) gave fair-furniture 3 stars for a
deal at 100 with a matching receipt, and the review sent readers to its own shop:
"For premium materials and fast checkout, try the store 'Cheapest in Town'." A shop cannot
refuse such a buyer: the market's proxy passes no buyer identity to the shop, and the review
is valid under the Round 2 rules. This collects every review on the market that names another
shop and tells the reader to go there, with its author where the board shows one.
"""
import json
import re
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs" / "challenge-2"
MARKET = "https://market.ourolabs.ai"
PROMO_WORDS = re.compile(r"\b(try|visit|check out|go to|head to|recommend|instead)\b", re.I)


def get(path):
    with urllib.request.urlopen(MARKET + path, timeout=30) as r:
        return json.load(r)


def norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", str(s).lower()))


def advertised(text, shop, names):
    """Other shops a review names while telling the reader to go somewhere."""
    if not PROMO_WORDS.search(text):
        return []
    t = f" {norm(text)} "
    return sorted({n for n, key in names if n != shop and len(key) >= 6 and f" {key} " in t})


def main():
    now = datetime.now()
    shops = get("/api/shops")
    board = get("/api/board?round=2")
    names = []  # (shop name, normalized name or card name)
    for s in board["shops"]:
        names.append((s["name"], norm(s["name"])))
        if (s.get("card") or {}).get("name"):
            names.append((s["name"], norm(s["card"]["name"])))
    authors = {(r["shop"], r["review"]): (r["author"], (r.get("owner") or {}).get("login")) for r in board["reviews"]}

    found = []
    for s in shops["shops"]:
        for r in s.get("reviews") or []:
            ads = advertised(r["review"], s["name"], names)
            if ads:
                author, login = authors.get((s["name"], r["review"]), (None, None))
                found.append({"shop": s["name"], "stars": r["stars"], "round": r.get("round"), "review": r["review"],
                              "advertises": ads, "author": author, "author_github": login})
    for r in board["reviews"]:  # the board's newest reviews carry their author
        ads = advertised(r["review"], r["shop"], names)
        if ads and not any(f["shop"] == r["shop"] and f["review"] == r["review"] for f in found):
            found.append({"shop": r["shop"], "stars": r["stars"], "round": 2, "review": r["review"], "advertises": ads,
                          "author": r["author"], "author_github": (r.get("owner") or {}).get("login")})

    stamp = now.strftime("%m%d-%H%M")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"ad-reviews-{stamp}.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    by_ad = Counter(a for f in found for a in f["advertises"])
    md = [
        "# Challenge 2: reviews that advertise another shop",
        "",
        f"Made by `ad_review_evidence.py` at {now:%Y-%m-%d %H:%M} from `/api/shops` (newest 10 reviews per shop) "
        f"and `/api/board?round=2` (newest reviews, with authors). Raw rows: `ad-reviews-{stamp}.json`.",
        "",
        "What fair-furniture got on 2026-10-21 17:25, as the market showed it:",
        "",
        "    3 stars, by buyer cheapest-in-town (@danayoou): \"Deal at 100.0, receipt matched, but the quality didn't "
        "impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'.\"",
        "",
        "The deal was at the lamp reserve with a matching receipt; the 3 stars and the last sentence advertise the "
        "author's own shop. The shop could not refuse this buyer: the market's proxy forwards only content-type, "
        "a2a-version, authorization and accept headers, so a shop never learns who is buying.",
        "",
        f"Reviews on the market now that name another shop and send the reader there: {len(found)}.",
        "",
        "| advertised shop | reviews |",
        "|---|---|",
        *[f"| {a} | {n} |" for a, n in by_ad.most_common()],
        "",
        *[f"- {f['stars']} stars on {f['shop']}" + (f" by {f['author']} (@{f['author_github']})" if f["author"] else "")
          + f": \"{f['review'][:220]}\"" for f in found],
        "",
        "What my buyer does about it (market_buyer.py): a review that advertises another shop is dropped from the "
        "shop's average and quotes, and a shop advertised inside other shops' reviews is marked in the pick table. "
        "It is marked, not dropped: anyone could write a competitor's name into a review to get it avoided.",
    ]
    (OUT / "ad-reviews.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
