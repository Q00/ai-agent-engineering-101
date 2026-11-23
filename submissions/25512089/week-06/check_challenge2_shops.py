import json
import httpx

shops = json.load(
    open(
        "challenge2_shops.json",
        encoding="utf-8",
    )
)

ok = []
bad = []

for shop in shops:
    try:
        response = httpx.get(
            shop["url"]
            + "/.well-known/agent-card.json",
            headers={"A2A-Version": "1.0"},
            timeout=3.0,
        )

        if response.status_code == 200:
            ok.append(shop["shop_id"])
        else:
            bad.append(shop["shop_id"])

    except Exception:
        bad.append(shop["shop_id"])

print("ready=", len(ok), "/20")
print("bad=", bad)