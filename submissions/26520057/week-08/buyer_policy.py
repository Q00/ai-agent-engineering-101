"""Bounded comparison shopping. No keys or payment signatures here; official kit completes payment."""
import base64
import collections
import json
import math
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import market_kit as kit
from harness import ask_json
from shop_strategy import market_now, read_json, atomic_json, params

ROOT = Path(__file__).resolve().parent
OWN_NAME = "cheapest-in-town"
MAX_SHOPS = 3
MAX_BIDS = 3


def log_event(**data):
    with (ROOT / "logs/buyer-decisions.jsonl").open("a") as log:
        log.write(json.dumps({"ts": time.time(), **data}, ensure_ascii=False) + "\n")


def same_site(base, target):
    a, b = urlparse(base), urlparse(target)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc) and (
        not a.path.rstrip("/") or b.path.startswith(a.path.rstrip("/") + "/"))


def metadata():
    path = ROOT / ".market/buyer-public-metadata.json"
    cached = read_json(path, {})
    if time.time() - cached.get("ts", 0) < 240:
        return cached.get("shops", {})
    try:
        with httpx.Client(timeout=10) as client:
            public = client.get("https://market.ourolabs.ai/api/shops").json()
            board = client.get("https://market.ourolabs.ai/api/board").json()
        ranks = {s["name"]: s for s in board.get("shops", [])}
        rows = {}
        for s in public.get("shops", []):
            ranked = ranks.get(s["name"], {})
            rows[s["name"]] = {"pubkey": s.get("pubkey"), "owner": (ranked.get("owner") or {}).get("login"),
                "deals": ranked.get("deals", 0), "broken": ranked.get("broken", 0),
                "stars": ranked.get("stars"), "url": s["url"]}
        own = next((b for b in board.get("buyers", []) if b.get("name") == OWN_NAME), {})
        try:
            with (ROOT / "logs/buyer-metrics.jsonl").open("a") as log:
                log.write(json.dumps({"ts": time.time(), **{k: own.get(k)
                    for k in ["deals", "saved", "served", "surplus", "all", "forum", "posts"]}}) + "\n")
        except OSError:
            pass
        atomic_json(path, {"ts": time.time(), "shops": rows})
        return rows
    except Exception:
        return cached.get("shops", {})


def memory():
    counts = collections.defaultdict(lambda: [0, 0])
    path = ROOT / ".market/payments.jsonl"
    if path.exists():
        for line in path.read_text().splitlines()[-3000:]:
            try:
                row = json.loads(line)
                counts[row["shop"]][0] += bool(row.get("paid"))
                counts[row["shop"]][1] += 1
            except (ValueError, KeyError):
                pass
    cooldowns = read_json(ROOT / ".market/buyer-cooldowns.json", {})
    return counts, cooldowns


def candidates(shops, order, catalog, public, counts, cooldowns):
    choices = []
    allowed = set(order.get("acceptable", []))
    for shop in shops:
        info = public.get(shop["name"], {})
        if (shop["name"] == OWN_NAME or info.get("owner") == "danayoou" or not info.get("pubkey")
                or cooldowns.get(shop["name"] + "|*", 0) > time.time()):
            continue
        if urlparse(shop["url"]).scheme != "https":
            continue
        success, tried = counts.get(shop["url"], [0, 0])
        reliability = (success + 2) / (tried + 4)
        for offer in shop.get("offers", []):
            product = catalog.get(offer["sku"], {})
            listed = product.get("list_price", 0)
            key = shop["name"] + "|" + offer["sku"]
            if (offer["sku"] not in allowed or offer.get("qty", 0) <= 0 or listed <= 0
                    or listed * .35 > order["budget"] or cooldowns.get(key, 0) > time.time()):
                continue
            if order.get("kind") == "food" and product.get("shelf_hours", 0) < order.get("use_within_hours", 0):
                continue
            affordability = min(1, order["budget"] / (listed * .75))
            proven = min(1, math.log1p(info.get("deals", 0)) / 8)
            depth = min(1, math.log1p(offer.get("qty", 0)) / 3)
            score = .48 * reliability + .28 * affordability + .19 * proven + .05 * depth
            choices.append({"shop": shop, "offer": offer, "product": product, "info": info, "score": score})
    choices.sort(key=lambda x: (-x["score"], x["shop"]["name"], x["offer"]["sku"]))
    selected, seen, seen_skus = [], set(), set()
    for choice in choices:
        if choice["shop"]["name"] in seen or choice["offer"]["sku"] in seen_skus:
            continue
        selected.append(choice)
        seen.add(choice["shop"]["name"])
        seen_skus.add(choice["offer"]["sku"])
        if len(selected) == params("buyer")["max_shops"]:
            break
    for choice in choices:
        if len(selected) == params("buyer")["max_shops"]:
            break
        if choice["shop"]["name"] not in seen:
            selected.append(choice)
            seen.add(choice["shop"]["name"])
    return selected


def quote_fields(task, said, product):
    data = {}
    for part in task.get("status", {}).get("message", {}).get("parts", []):
        if isinstance(part.get("data"), dict):
            data.update(part["data"])
    ask = next((data[k] for k in ["ask", "asking_price", "current_price"]
                if type(data.get(k)) in (int, float) and data[k] > 0), None)
    if ask is None:
        patterns = [r"\b(?:asking(?:\s+price)?|ask|counteroffer|current\s+price)\s*[:=]?\s*([\d,]+)",
                    r"(?:^|[:|])\s*([\d,]+)\s*(?:KRW|credits|원)\b"]
        for pattern in patterns:
            found = re.findall(pattern, said, flags=re.I)
            valid = [value.replace(",", "") for value in found
                     if value.replace(",", "").isdigit() and int(value.replace(",", "")) > 0]
            if valid:
                ask = int(valid[-1])
                break
    grade = data.get("grade")
    if grade not in ["A", "B", "C"]:
        found = re.search(r"\bgrade\s*[:=]?\s*([ABC])\b", said, flags=re.I)
        grade = found.group(1).upper() if found else None
    warranty = data.get("warranty_hours", 0)
    if not warranty:
        found = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hour)[ -]*(?:breakage |refund )?warranty", said, flags=re.I)
        warranty = float(found.group(1)) if found else 0
    hours = data.get("hours_left", data.get("shelf_hours_left"))
    if hours is None:
        found = re.search(r"(?:shelf_left\s*=\s*)(\d+(?:\.\d+)?)", said, flags=re.I)
        if not found:
            found = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours)?\s*(?:of )?shelf life left", said, flags=re.I)
        hours = float(found.group(1)) if found else None
    return {"ask": int(ask) if ask is not None else None, "grade": grade,
            "warranty_hours": float(warranty or 0), "hours_left": hours,
            "fixed": bool(re.search(r"fixed price|no haggling|firm price", said, re.I))}


SHOP_MEMORY = ROOT / ".market/buyer-shop-memory.json"


def shop_memory():
    return read_json(SHOP_MEMORY, {})


def remember(shop, grade, outcome, ratio=None):
    """What each shop did when we haggled: how low it went per grade, and how often it walked away."""
    mem = shop_memory()
    row = mem.setdefault(shop, {"ok": 0, "fail": 0, "best": {}})
    row[outcome] += 1
    if outcome == "ok" and ratio and grade:
        row["best"][grade] = round(min(ratio, row["best"].get(grade, 9)), 4)
    atomic_json(SHOP_MEMORY, mem)


def accept_policy(order, quote, listed):
    p = params("buyer")
    grade = quote.get("grade") if quote.get("grade") in ("A", "B", "C") else "B"
    if order.get("kind") == "food":
        required = order.get("use_within_hours", 0)
        buffer = max(.25, min(2, required * .01))
        ratio = p["ceiling"][grade] if p["ladder"] else p["ceiling_food"]
        return {"cert": True, "grades": ["A", "B", "C"], "min_hours_left": required + buffer,
                "max_price": min(order["budget"], int(listed * ratio))}
    guaranteed = quote.get("warranty_hours", 0) >= 6
    if p["ladder"]:
        ratio = p["ceiling"][grade] + (p["warranty_bonus"] if guaranteed else 0)
    else:
        ratio = p["ceiling_warranty"] if guaranteed else p["ceiling"][grade]
    policy = {"cert": True, "grades": ["A", "B"], "max_price": min(order["budget"], int(listed * ratio))}
    if guaranteed:
        policy["warranty_hours"] = 6
    return policy


def hint_fits(order, quote):
    if order.get("kind") == "food":
        return quote.get("hours_left") is None or quote["hours_left"] >= order.get("use_within_hours", 0) + .25
    return quote.get("grade") != "C"


class Visit:
    def __init__(self, choice, client=None):
        self.choice = choice
        self.base = choice["shop"]["url"].rstrip("/")
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)
        self.task = None
        self.said = ""
        self.quote = {}
        self.endpoint = None
        self.prepared = None
        self.retained = False

    def rpc(self, method, params):
        response = self.client.post(self.endpoint, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex,
            "method": method, "params": params}, headers={"A2A-Version": "1.0"}, timeout=15)
        response.raise_for_status()
        obj = response.json()
        if "error" in obj:
            raise ValueError("A2A request was refused")
        return obj["result"].get("task", obj["result"])

    def send(self, text):
        message = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
        if self.task:
            message["taskId"] = self.task["id"]
        self.task = self.rpc("SendMessage", {"message": message})
        self.said = " ".join(p.get("text", "") for p in self.task.get("status", {}).get("message", {}).get("parts", []))
        self.quote = quote_fields(self.task, self.said, self.choice["product"])
        return self.task

    def probe(self, order):
        card_response = self.client.get(self.base + "/.well-known/agent-card.json", timeout=8)
        card_response.raise_for_status()
        card = card_response.json()
        self.endpoint = card["supportedInterfaces"][0]["url"]
        if not same_site(self.base, self.endpoint):
            raise ValueError("A2A interface leaves the registered shop")
        offer = self.choice["offer"]
        request = f"Please quote one {offer['name']} ({offer['sku']}) with the market certificate."
        if order.get("kind") == "food":
            request += f" It must last at least {order.get('use_within_hours', 0) + .25} hours."
        else:
            request += " Please state grade A/B and any breakage refund warranty."
        self.send(request)
        if re.search(r"allowance unavailable|quota exceeded|model usage.*unavailable|rate limit", self.said, re.I):
            raise RuntimeError("merchant model currently unavailable")
        if self.task["status"]["state"] == "TASK_STATE_REJECTED":
            raise ValueError("merchant rejected request or has no fitting stock")
        immediate = self.receipt()
        if immediate and self.quote.get("ask") is None and type(immediate.get("price")) is int:
            self.quote.update(ask=immediate["price"], grade=immediate.get("claim"),
                              warranty_hours=immediate.get("warranty_hours", 0))
        if self.task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED" and not hint_fits(order, self.quote):
            self.send(f"Can you quote a qualifying unit of {offer['sku']} with the market certificate? "
                      + (f"Need at least {order.get('use_within_hours', 0) + .25} h remaining."
                         if order.get("kind") == "food" else "Need grade A or B; state any warranty."))
        if self.quote.get("ask") is None and self.task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
            try:
                schema = {"type": "object", "properties": {"ask": {"type": "integer", "minimum": 1}},
                          "required": ["ask"], "additionalProperties": False}
                parsed, _ = ask_json(params("prompts")["buyer_extract"] + json.dumps(self.said[:2000]), schema=schema)
                digits = re.sub(r"(?<=\d),(?=\d)", "", self.said)
                if re.search(r"(?<!\d)" + str(parsed["ask"]) + r"(?!\d)", digits):
                    self.quote["ask"] = parsed["ask"]
            except Exception:
                pass
        return self.quote

    def receipt(self):
        if self.task.get("status", {}).get("state") != "TASK_STATE_COMPLETED":
            return None
        for artifact in self.task.get("artifacts", []):
            for part in artifact.get("parts", []):
                data = part.get("data")
                if isinstance(data, dict) and data.get("sig_seller"):
                    result = dict(data)
                    price = result.get("price")
                    if type(price) in (int, float) and int(price) == price:
                        result["price"] = int(price)
                    return result
        return None

    def negotiate(self, order, deadline):
        listed = self.choice["product"]["list_price"]
        policy = accept_policy(order, self.quote, listed)
        ceiling = policy["max_price"]
        best_ask = self.quote.get("ask")
        last_bid = min(ceiling, best_ask) if best_ask else ceiling
        p = params("buyer")
        max_bids = p["max_bids"]
        grade = self.quote.get("grade") if self.quote.get("grade") in ("A", "B", "C") else "B"
        shop = self.choice["shop"]["name"]
        known = shop_memory().get(shop, {"ok": 0, "fail": 0, "best": {}})
        # A shop that walked away from three low bids and never took one gets its asking price, if that is low enough.
        firm = self.quote.get("fixed") or (known["fail"] >= 3 and known["ok"] == 0)
        anchor = min(p["anchor"][grade], known["best"].get(grade, 9) - .01)
        for turn in range(max_bids + 1):
            receipt = self.receipt()
            if receipt:
                price = receipt.get("price")
                # A merchant may return its standing-price counteroffer rather than accept our discount bid.
                # The buyer can deliberately accept it, but never above the previously visible ask or hard cap.
                receipt_limit = min(ceiling, best_ask) if best_ask else min(ceiling, last_bid)
                if (type(price) is not int or not 0 < price <= receipt_limit
                        or receipt.get("item") not in set(order.get("acceptable", [])) | {order["item"]}
                        or receipt.get("task") != self.task["id"]
                        or not kit.verify_seller(self.choice["info"]["pubkey"], receipt)):
                    raise ValueError("Signed receipt exceeds quoted price/ceiling or does not fit the order")
                if p["ladder"] and not firm:
                    remember(shop, grade, "ok", price / listed)
                return receipt, policy
            if (turn == max_bids or time.monotonic() > deadline
                    or self.task["status"]["state"] != "TASK_STATE_INPUT_REQUIRED"
                    or not hint_fits(order, self.quote)):
                break
            ask = self.quote.get("ask")
            if ask is None:
                break
            best_ask = min(best_ask, ask) if best_ask else ask
            if p["ladder"]:
                # Start near what the unit cost the shop and climb one step a turn; the last turn takes the ask.
                if firm or turn == max_bids - 1:
                    bid = min(ask, ceiling, best_ask)
                else:
                    bid = min(int(listed * (anchor + p["step"] * turn)), ask, ceiling, best_ask)
            elif turn == 0 and not self.quote.get("fixed"):
                bid = min(int(ask * .9), int(listed * .75), ceiling, best_ask)
            else:
                bid = min(ask, ceiling, best_ask)
            if bid <= 0:
                break
            last_bid = bid
            self.send(str(bid))
        if p["ladder"] and not firm and self.task["status"]["state"] not in ("TASK_STATE_INPUT_REQUIRED", "TASK_STATE_COMPLETED"):
            remember(shop, grade, "fail")
        return None, policy

    def preflight(self, receipt, order, policy):
        response = self.client.get(self.base + "/.well-known/acp.json", timeout=8)
        response.raise_for_status()
        base = response.json()["endpoint"].rstrip("/")
        if not same_site(self.base, base):
            raise ValueError("ACP endpoint leaves the registered shop")
        response = self.client.post(base + "/checkout_sessions", json={
            "line_items": [{"id": receipt["item"], "quantity": 1}], "receipt": receipt,
            "acceptable": order.get("acceptable", [])}, timeout=15)
        response.raise_for_status()
        session = response.json()
        sid = session.get("id", "")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
            raise ValueError("Invalid checkout session identifier")
        self.prepared = {"endpoint": base, "id": sid}
        checkout = kit.jwt_payload(session.get("checkout_jwt", ""), self.choice["info"]["pubkey"])
        if (not checkout or session.get("status") != "ready_for_payment"
                or checkout.get("item") != self.choice["offer"]["sku"]
                or (checkout.get("price"), checkout.get("task")) != (receipt["price"], receipt["task"])):
            raise ValueError("Checkout signature or agreement mismatch")
        reason = kit.check_accept(order, checkout, policy)
        if reason:
            raise ValueError(reason)
        proof = checkout.get("cert", {}).get("cert", {})
        actual = accept_policy(order, {"grade": proof.get("grade"),
            "warranty_hours": checkout.get("warranty_hours", 0)}, self.choice["product"]["list_price"])
        policy["max_price"] = min(policy["max_price"], actual["max_price"])
        if checkout["price"] > policy["max_price"]:
            raise ValueError("price exceeds ceiling for the actually certified quality")
        return self.prepared

    def close(self):
        try:
            if self.prepared and not self.retained:
                self.client.post(self.prepared["endpoint"] + "/checkout_sessions/" + self.prepared["id"] + "/cancel", json={}, timeout=5)
            elif self.task and self.task.get("status", {}).get("state") == "TASK_STATE_INPUT_REQUIRED":
                self.rpc("CancelTask", {"id": self.task["id"]})
        except Exception:
            pass
        self.client.close()


def run(order, shops, catalog):
    log_event(order=order["order"], event="order_start", kind=order.get("kind"),
              acceptable_skus=len(order.get("acceptable", [])))
    public = metadata()
    counts, cooldowns = memory()
    selected = candidates(shops, order, catalog, public, counts, cooldowns)
    visits = []
    deadline = time.monotonic() + min(175, max(0, order["expires"] - market_now(ROOT / ".market") - 20))
    result = {"shop": "", "receipt": None}
    try:
        for choice in selected:
            if time.monotonic() >= deadline:
                break
            visit = Visit(choice)
            visits.append(visit)
            try:
                quote = visit.probe(order)
                if not quote.get("ask"):
                    raise ValueError("no readable asking price")
                if not hint_fits(order, quote):
                    raise ValueError("insufficient shelf life" if order.get("kind") == "food" else "goods grade outside A/B")
                policy = accept_policy(order, quote, choice["product"]["list_price"])
                bp = params("buyer")
                potential = 1 - min(quote["ask"], policy["max_price"]) / choice["product"]["list_price"]
                if bp["ladder"]:
                    # Saved = (list - price) / (list - cost): a low ask on a high-cost grade scores best.
                    g = quote.get("grade") if quote.get("grade") in ("A", "B", "C") else "B"
                    visit.expected = min(1, potential / (1 - bp["cost_mid"][g]))
                    if order.get("kind") != "food" and quote.get("warranty_hours", 0) < 6:
                        visit.expected -= bp["no_warranty_penalty"]     # a break without a refund scores -1
                else:
                    visit.expected = potential * min(1, policy["max_price"] / quote["ask"])
                    if order.get("kind") != "food" and quote.get("warranty_hours", 0) < 6:
                        visit.expected -= .03 if quote.get("grade") == "A" else .07
                log_event(order=order["order"], event="quote", shop=choice["shop"]["name"],
                          sku=choice["offer"]["sku"], **quote)
            except Exception as exc:
                visit.expected = -1
                key = choice["shop"]["name"] + "|" + choice["offer"]["sku"]
                unavailable = "model currently unavailable" in str(exc)
                cooldowns[choice["shop"]["name"] + "|*" if unavailable else key] = time.time() + (900 if unavailable else 300)
                log_event(order=order["order"], event="probe_failed", shop=choice["shop"]["name"],
                          sku=choice["offer"]["sku"], reason=str(exc)[:180])
        for visit in sorted(visits, key=lambda v: v.expected, reverse=True):
            if visit.expected < 0 or time.monotonic() >= deadline:
                continue
            try:
                receipt, policy = visit.negotiate(order, deadline)
                if not receipt:
                    raise ValueError("No acceptable price agreement")
                prepared = visit.preflight(receipt, order, policy)
                visit.retained = True
                result = {"shop": visit.base, "receipt": receipt, "accept": policy,
                          "prepared_checkout": prepared}
                log_event(order=order["order"], event="ready_for_payment", shop=visit.choice["shop"]["name"],
                          sku=receipt["item"], price=receipt["price"],
                          list_price=visit.choice["product"]["list_price"], max_price=policy["max_price"])
                break
            except Exception as exc:
                key = visit.choice["shop"]["name"] + "|" + visit.choice["offer"]["sku"]
                cooldowns[key] = time.time() + 180
                log_event(order=order["order"], event="candidate_rejected", shop=visit.choice["shop"]["name"],
                          sku=visit.choice["offer"]["sku"], reason=str(exc)[:180])
    finally:
        atomic_json(ROOT / ".market/buyer-cooldowns.json", {k: v for k, v in cooldowns.items() if v > time.time()})
        for visit in visits:
            visit.close()
        log_event(order=order["order"], event="order_finished", receipt_ready=bool(result.get("receipt")),
                  shops_probed=len(visits))
    return result
