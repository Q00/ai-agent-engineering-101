"""Week 06 challenge 1 — catalog_search.py: parts 2-3 of design/challenge-1-search.md.

    python catalog_search.py "something wooden to keep about 40 books, not taller than my desk"

parse_request(): one harness call turns the buyer's sentence into a fixed JSON query.
search():        code only -- hard filters on the catalog fields, then a closeness score,
                 then the top five. The harness never sees the catalog here.

v2 (after devlogs/challenge1-search-v1-1018.txt):
  - keywords match whole words only, and never the item's colour word ("light" vs "Light Oak")
  - style must be one of the catalog's four styles; anything else becomes null
  - the harness also picks subtypes (a reading lamp for bed -> clip, desk), a hard filter
  - a request that leaves many matches and names no size, capacity or subtype is flagged vague,
    so the curator can ask the buyer one question instead of guessing
"""
import json
import math
import os
import re
import sys

from harness import ask
from make_catalog import CATEGORIES, STYLES

HERE = os.path.dirname(os.path.abspath(__file__))
CAPACITY_FIELD = {"bookshelf": "books", "desk": "monitors", "office chair": "max_load_kg",
                  "dining chair": "seats", "sofa": "seats", "bed": "sleeps", "wardrobe": "hanging_m",
                  "chest of drawers": "drawers", "table": "seats", "lamp": "lumens"}
SUBTYPES = {cat: list(spec["subtypes"]) for cat, spec in CATEGORIES.items()}
VAGUE_MATCHES = 50   # more matches than this, with nothing concrete asked, is a vague request

PARSE_PROMPT = """A buyer is describing furniture they want from a shop. Turn the description into a search query.
Categories and their subtypes:
{subtypes}
Materials in the catalog: oak, pine, walnut, particleboard, metal, mesh, fabric, leather, velvet, glass.
("wooden" or "wood" means oak, pine and walnut; "solid wood" means oak and walnut.)
Styles in the catalog: scandinavian, industrial, classic, modern. Use one of these or null; nothing else.
Subtypes: list the subtypes of the category that fit what the buyer will use it for (for example,
a lamp to read in bed -> ["clip", "desk"]); leave the list empty if any subtype would do.
Capacity is the amount the buyer needs, in the category's unit: bookshelf = books, desk = monitors,
office chair = max load in kg, dining chair / sofa / table = seats, bed = people it sleeps,
wardrobe = metres of hanging rail, chest of drawers = drawers, lamp = lumens.
Turn relative sizes into centimetres with typical sizes: a desk or table is about 75 cm high,
a kitchen counter 90 cm, a bed 50 cm, a window sill 90 cm, a standard door 200 cm.
Use null when the buyer does not say something. Do not guess a category from nothing.

Buyer's description: {request}

Reply with one JSON object only:
{{"category": <category or null>, "subtypes": [<subtypes>], "materials": [<materials>],
 "color": <string or null>, "style": <style or null>,
 "max_height_cm": <int or null>, "max_width_cm": <int or null>, "max_depth_cm": <int or null>,
 "capacity": <number or null>, "rooms": [<rooms>], "keywords": [<a few words from the request>]}}"""


def load_catalog(path=os.path.join(HERE, "catalog.json")):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_request(request):
    """Part 2: one harness call. Returns the query dict, cleaned to the catalog's vocabulary."""
    subtypes_text = "\n".join(f"  {cat}: {', '.join(subs)}" for cat, subs in SUBTYPES.items())
    q = ask(PARSE_PROMPT.format(subtypes=subtypes_text, request=request))
    for key in ("subtypes", "materials", "rooms", "keywords"):
        q[key] = q.get(key) or []
    if q.get("category") not in CAPACITY_FIELD:
        q["category"] = None
    if q.get("style") not in STYLES:                     # e.g. "cozy" is not a catalog style
        q["style"] = None
    q["subtypes"] = [s for s in q["subtypes"] if q["category"] and s in SUBTYPES[q["category"]]]
    return q


def _passes(item, q, relax):
    if q.get("category") and item["category"] != q["category"]:
        return False
    if q["subtypes"] and "subtypes" not in relax and item["subtype"] not in q["subtypes"]:
        return False
    if q["materials"] and "materials" not in relax and item["material"] not in q["materials"]:
        return False
    widen = 1.1 if "sizes" in relax else 1.0
    for dim in ("height", "width", "depth"):
        limit = q.get(f"max_{dim}_cm")
        if limit and item[f"{dim}_cm"] > limit * widen:
            return False
    cap_field = CAPACITY_FIELD.get(item["category"])
    if q.get("capacity") and cap_field in item and item[cap_field] < 0.9 * float(q["capacity"]):
        return False
    return True


def _match_text(item):
    """Text the keywords are matched against: everything but the colour word."""
    text = " ".join([item["subtype"], item["category"], item["description"],
                     " ".join(item["features"]), " ".join(item["rooms"])]).lower()
    return re.sub(rf"\b{re.escape(item['color'].lower())}\b", " ", text)


def _score(item, q, relax):
    """Lower is better: closeness to the asked capacity, then soft matches."""
    s = 0.0
    cap_field = CAPACITY_FIELD.get(item["category"])
    if q.get("capacity") and item.get(cap_field):
        s += abs(math.log(item[cap_field] / float(q["capacity"])))
    if "soft" not in relax:
        if q.get("color") and q["color"].lower() != item["color"]:
            s += 0.3
        if q.get("style") and q["style"] != item["style"]:
            s += 0.3
        if q["rooms"] and not set(r.lower() for r in q["rooms"]) & set(item["rooms"]):
            s += 0.2
    text = _match_text(item)
    s -= 0.05 * sum(1 for k in q["keywords"] if re.search(rf"\b{re.escape(k.lower())}\b", text))
    return s


def search(catalog, q, k=5):
    """Part 3: returns (top k items, number of matches, relaxations applied)."""
    steps = [set(), {"soft"}, {"soft", "sizes"}, {"soft", "sizes", "subtypes"},
             {"soft", "sizes", "subtypes", "materials"}]
    for relax in steps:
        matches = [it for it in catalog if _passes(it, q, relax)]
        if matches:
            matches.sort(key=lambda it: (_score(it, q, relax), it["id"]))
            return matches[:k], len(matches), sorted(relax)
    return [], 0, sorted(steps[-1])


def is_vague(q, n_matches):
    """Many matches and nothing concrete asked: better to ask the buyer than to guess."""
    concrete = q.get("capacity") or q["subtypes"] or any(q.get(f"max_{d}_cm") for d in ("height", "width", "depth"))
    return n_matches > VAGUE_MATCHES and not concrete


def summary(item):
    """One short line per candidate, as the curator will see it (no reserve, no price)."""
    cap_field = CAPACITY_FIELD[item["category"]]
    cap = f", {cap_field} {item[cap_field]}" if cap_field in item else ""
    return (f"{item['id']}: {item['name']} -- {item['material']}, {item['color']}, {item['style']}, "
            f"W{item['width_cm']} D{item['depth_cm']} H{item['height_cm']} cm{cap}"
            + (f", {', '.join(item['features'])}" if item["features"] else ""))


if __name__ == "__main__":
    request = " ".join(sys.argv[1:]) or "something wooden to keep about 40 books, not taller than my desk"
    print("request :", request)
    q = parse_request(request)
    print("query   :", json.dumps(q, ensure_ascii=False))
    top, n, relaxed = search(load_catalog(), q)
    print(f"matches : {n}" + (f" (relaxed: {', '.join(relaxed)})" if relaxed else "")
          + ("  -- VAGUE: the curator should ask the buyer one question" if is_vague(q, n) else ""))
    for it in top:
        print("  ", summary(it))
