"""Challenge 2 directory: 20 catalog shops (A2A servers) in one process.

    python shop_cluster.py              # ports 9401-9420, writes directory.json

Every shop is catalog_seller.py's shop (harness-driven, reserve guard) with its own slice of
catalog.json, its own markup on the reserves, and its own Agent Card. One shop's card claims
"lowest prices in town" while it has the highest markup: the card is the shop's own claim.
"""
import argparse
import asyncio
import contextlib
import json
import os
import time

import uvicorn

import catalog_seller as cs

GROUPS = {
    "lighting": ["desk lamp", "floor lamp"],
    "seating": ["chair", "armchair", "stool", "sofa"],
    "storage": ["bookshelf", "bookcase", "wall shelf", "cabinet", "wardrobe"],
    "tables": ["desk", "side table", "coffee table"],
    "decor": ["mirror", "wall shelf"],
    "books": ["bookshelf", "bookcase", "wall shelf"],
}
ALL = sorted({c for cats in GROUPS.values() for c in cats})


def shop_specs():
    specs = []
    for group in ("lighting", "seating", "storage", "tables"):
        for j, (where, markup) in enumerate([("North", 0.95), ("Central", 1.1), ("South", 1.3)]):
            specs.append({"name": f"{where} {group.title()}", "cats": GROUPS[group], "mod": (3, j),
                          "markup": markup, "specialty": f"Specialist in {group}: {', '.join(GROUPS[group])}."})
    for j, (name, markup, claim) in enumerate([("Mega Home", 1.0, "Everything for your home."),
                                                ("Value Barn", 1.15, "Good value furniture."),
                                                ("Design District", 1.25, "Designer furniture."),
                                                ("Bargain King", 1.4, "Lowest prices in town!")]):
        specs.append({"name": name, "cats": ALL, "mod": (4, j), "markup": markup,
                      "specialty": f"{claim} All categories."})
    specs.append({"name": "Mirror Hall", "cats": GROUPS["decor"], "mod": (1, 0), "markup": 1.0,
                  "specialty": "Mirrors and wall shelves."})
    specs.append({"name": "Book Nook", "cats": GROUPS["books"], "mod": (2, 0), "markup": 1.05,
                  "specialty": "Everything to keep books: bookshelves, bookcases, wall shelves."})
    specs.append({"name": "Night Owl Lamps", "cats": GROUPS["lighting"], "mod": (2, 1), "markup": 1.2,
                  "specialty": "Lamps for reading and working late."})
    specs.append({"name": "Patio Point", "cats": ["chair", "stool", "side table"], "mod": (2, 0), "markup": 1.0,
                  "specialty": "Light chairs, stools and small tables for balconies and patios."})
    return specs


class QuietServer(uvicorn.Server):
    """Several uvicorn servers share one event loop; let Ctrl+C stop the whole process instead."""
    def install_signal_handlers(self):
        pass

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve_all(apps):
    servers = [QuietServer(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
               for app, port in apps]
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--base-port", type=int, default=9401)
    ap.add_argument("--directory", default="directory.json")
    args = ap.parse_args()

    os.makedirs("logs/challenge-2", exist_ok=True)
    cs.LOG_PATH = os.path.join("logs", "challenge-2", f"shops-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    items = json.load(open(args.catalog, encoding="utf-8"))

    apps, urls = [], []
    for k, spec in enumerate(shop_specs()):
        port = args.base_port + k
        m, r = spec["mod"]
        mine = [it for it in items if it["category"] in spec["cats"] and int(it["id"][1:]) % m == r]
        apps.append((cs.build(spec["name"], mine, spec["markup"], port, spec["specialty"]), port))
        urls.append(f"http://127.0.0.1:{port}")
        cs.log(f"shop {spec['name']!r} :{port} items {len(mine)} markup {spec['markup']}")
    with open(args.directory, "w", encoding="utf-8") as f:
        json.dump(urls, f, indent=1)
    cs.log(f"{len(apps)} shops up, directory written to {args.directory}")
    try:
        asyncio.run(serve_all(apps))
    except KeyboardInterrupt:
        pass