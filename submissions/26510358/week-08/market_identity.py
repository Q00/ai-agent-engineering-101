"""Ownership evidence supplied by the Market, never inferred from shop prose."""
from copy import deepcopy
import time


def cached_board(market, *, ttl=60):
    """One short-lived public lookup; stale or unavailable identity is unknown."""
    now = time.monotonic()
    cached = getattr(market, "_identity_board", None)
    if cached and now - cached[0] < ttl:
        return cached[1]
    try:
        response = market.call("GET", "/api/board")
        response.raise_for_status()
        board = response.json()
        if not isinstance(board, dict):
            return None
        market._identity_board = (now, board)
        return board
    except Exception:
        return None


def _login(row):
    owner = row.get("owner")
    value = owner.get("login") if isinstance(owner, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def enrich_shops(shops, board, *, buyer_name, own_shop_key=None, round_number=None):
    """Return candidates and exclusion reasons without asserting unknown owners.

    A shop's registry name AND URL must match the public board. Reviews remain
    untrusted text even when their author identity is known to the Market.
    """
    board = board if isinstance(board, dict) else {}
    owners = {}
    for row in board.get("buyers", []):
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            owners[row["name"]] = _login(row)
    own_owner = owners.get(buyer_name)
    registered = {}
    for row in board.get("shops", []):
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        card = row.get("card") or {}
        if isinstance(card, dict) and isinstance(card.get("url"), str):
            registered[(row["name"], card["url"].rstrip("/"))] = _login(row)
    records, excluded = [], []
    for shop in shops:
        name, url = shop["name"], shop["url"]
        owner = registered.get((name, url.rstrip("/")))
        reason = ("same_registered_shop_key" if own_shop_key and shop.get("pubkey") == own_shop_key else
                  "same_market_owner" if own_owner and owner and own_owner.casefold() == owner.casefold() else None)
        if reason:
            excluded.append({"name": name, "reason": reason})
            continue
        reviews = []
        for review in shop.get("reviews", []):
            if not isinstance(review, dict):
                continue
            copied = deepcopy(review)
            # Discard any pre-existing field before attaching our Market lookup.
            copied.pop("registry_author_owner", None)
            author_owner = owners.get(review.get("author"))
            if author_owner:
                copied["registry_author_owner"] = author_owner
            reviews.append(copied)
        records.append({"name": name, "url": url, "reviews": reviews,
                        "registry_owner": owner, "registry_owner_verified": owner is not None,
                        "current_round": round_number})
    return records, excluded
