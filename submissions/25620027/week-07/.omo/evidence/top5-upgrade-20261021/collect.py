import concurrent.futures
import datetime
import json
import pathlib
import urllib.request

HERE = pathlib.Path(__file__).parent


def fetch(label, url):
    started = datetime.datetime.now(datetime.UTC).isoformat()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AX-course-public-research/1.0"})
        with urllib.request.urlopen(request, timeout=25) as response:
            content = response.read(4_000_000)
            status = response.status
        (HERE / f"{label}.raw").write_bytes(content)
        value = json.loads(content)
    except Exception as error:
        status = str(error)
        value = None
    (HERE / f"{label}.meta.json").write_text(json.dumps({"url": url, "observed_at": started, "status": status}, indent=2))
    return label, value


if __name__ == "__main__":
    endpoints = [(name, "https://market.ourolabs.ai/api/" + name) for name in ("board", "shops", "stats")]
    endpoints.append(("comparison", "https://ax-week07.treasuresky.xyz/price-comparison.json"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        data = dict(pool.map(lambda pair: fetch(*pair), endpoints))
    board = data["board"]
    selected = board["shops"][:5]
    own = next(shop for shop in board["shops"] if shop["name"] == "25620027")
    print(json.dumps({"round": board["round"], "top5": selected, "own_rank": board["shops"].index(own) + 1, "own": own}, ensure_ascii=False, indent=2))
    registry = data["shops"]["shops"]
    urls = {shop["name"]: shop["url"].rstrip("/") for shop in registry}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        cards = list(pool.map(lambda shop: fetch("card-" + shop["name"], urls[shop["name"]] + "/.well-known/agent-card.json"), selected))
    print(json.dumps({"cards": dict(cards)}, ensure_ascii=False, indent=2))
