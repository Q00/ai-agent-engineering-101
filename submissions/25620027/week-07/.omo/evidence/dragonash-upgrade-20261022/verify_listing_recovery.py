from datetime import UTC, datetime
import json
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
BASE = "https://ax-week07.treasuresky.xyz"
MARKET = "https://market.ourolabs.ai"


def get(url):
    request = Request(url, headers={"User-Agent": "AX7-public-research"})
    with urlopen(request, timeout=25) as response:
        return json.load(response)


if __name__ == "__main__":
    before_board = json.loads((HERE / "final-board.raw").read_bytes())
    before = next(x for x in before_board["shops"] if x["name"] == "25620027")
    original_registry = json.loads((HERE / "shops.raw").read_bytes())
    original = next(x for x in original_registry["shops"] if x["name"] == "25620027")
    board = get(MARKET + "/api/board")
    registry = get(MARKET + "/api/shops")
    card = get(BASE + "/.well-known/agent-card.json")
    after = next(x for x in board["shops"] if x["name"] == "25620027")
    registered = next(x for x in registry["shops"] if x["name"] == "25620027")
    assert after["owner"]["login"] == before["owner"]["login"] == "SungJinho"
    assert registered["url"] == original["url"] == BASE
    assert registered["pubkey"] == original["pubkey"]
    assert after["card"]["version"] == card["version"] == "1.6.0"
    assert after["deals"] >= before["deals"]
    assert after["joined"] == before["joined"]
    result = {
        "verified_at": datetime.now(UTC).isoformat(), "round": board["round"],
        "rank": board["shops"].index(after) + 1,
        "shop": {key: after[key] for key in (
            "name", "owner", "joined", "card", "alive", "deals", "visits",
            "stars", "reviews", "revenue", "profit",
        )},
        "previous_deals": before["deals"], "same_registered_public_key": True,
        "same_url_and_owner": True, "same_join_timestamp": True,
        "server_and_listing_version": "1.6.0", "authenticated_ui_submit_count": 1,
        "buyer_started": False, "credentials_exported": False,
        "authentication_pending": False,
    }
    (HERE / "listing-recovery.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
