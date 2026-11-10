"""A2A negotiation for one selected, in-stock Round 3 SKU."""

from __future__ import annotations

import re
import time
import uuid

import httpx

from harness import decide_json, trace
from context_compaction import MAX_TURNS, text
import buyer_identity
from buyer_policy import acceptance, quote_shelf_minimum
from market_scoring import saved_at_price
import decision_audit as audit


def opening_request(name: str, sku: str, order: dict) -> str:
    policy = acceptance(order)
    note = 'A market-signed unit certificate is required.'
    if order.get('kind') == 'food':
        quote_hours=f"{quote_shelf_minimum(order):.6f}".rstrip('0').rstrip('.')
        note += (f" The unit must have at least {quote_hours} hours of shelf life left when quoting "
                 f"(includes two minutes for checkout). Checkout requires {policy['min_hours_left']:g} hours.")
    else:
        note += ' Only grade A or B is acceptable. A six-hour warranty is preferred, not required.'
    return f'I want to buy {name} ({sku}). {note}'


def status(task: dict) -> str:
    return task.get("status", {}).get("state", "")


def status_text(task: dict) -> str:
    parts = task.get("status", {}).get("message", {}).get("parts", [])
    return " ".join(p.get("text", "") for p in parts)


def asking_price(message: str) -> int | None:
    match = re.search(r"(?:asking|counteroffer|current ask)\s*:?\s*(\d+)", message, re.I)
    return int(match.group(1)) if match else None


def card_endpoint(client: httpx.Client, shop_url: str, deadline: float) -> str:
    response = client.get(shop_url.rstrip("/") + "/.well-known/agent-card.json",
                          timeout=min(15, max(1, deadline - time.monotonic())))
    response.raise_for_status()
    card = response.json()
    for interface in card.get("supportedInterfaces", []):
        if interface.get("protocolBinding") == "JSONRPC" and str(interface.get("protocolVersion")) == "1.0":
            return interface["url"]
    raise ValueError("shop has no A2A 1.0 JSON-RPC interface")


def send(client: httpx.Client, endpoint: str, message: str, deadline: float,
         task_id: str | None = None,identity: dict | None = None) -> dict:
    body = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": message}]}
    if task_id:
        body["taskId"] = task_id
    params={'message':body}
    if identity:
        params['metadata']={'buyer_identity':buyer_identity.sign(identity['key'],identity['name'],identity['recipient'],
                                                                 body['messageId'],task_id,message)}
    response = client.post(endpoint, json={"jsonrpc": "2.0", "id": str(uuid.uuid4()),
                                           "method": "SendMessage", "params": params},
                           headers={"A2A-Version": "1.0"},
                           timeout=min(85, max(1, deadline - time.monotonic())))
    response.raise_for_status()
    answer = response.json()
    if "error" in answer:
        raise ValueError(f"A2A error: {str(answer['error'])[:250]}")
    return answer["result"].get("task", answer["result"])


def get_receipt(task: dict) -> dict | None:
    for artifact in task.get("artifacts", []):
        for part in artifact.get("parts", []):
            data = part.get("data")
            if isinstance(data, dict) and data.get("sig_seller"):
                if isinstance(data.get("price"), float) and data["price"].is_integer():
                    data = dict(data, price=int(data["price"]))
                return data
    return None


def followup_offer(*, message: str, last_offer: int, cap: int, sku: str,
                   list_price: int, memory: dict, deadline: float,
                   history: list[dict] | None = None,
                   offers_remaining: int | None = None) -> int | None:
    audit.begin('buyer_counter',parent_decision_id=audit.links().get('decision_id'))
    ask = asking_price(message)
    price_guidance = (memory or {}).get('price_guidance') or {}
    suggested = price_guidance.get('suggested_maximum')
    fallback_cap = cap
    if type(suggested) is int and last_offer < suggested < cap and (offers_remaining or 0) > 1:
        fallback_cap = suggested
    fallback = (ask if ask is not None and ask <= fallback_cap else
                min(fallback_cap, last_offer + max(1, (fallback_cap - last_offer) // 2)))
    if fallback <= last_offer and (ask is None or ask > cap):
        audit.proposed({'action':'walk','price':None},source='policy',reason='no higher authorized offer')
        return None
    if deadline - time.monotonic() < 70:
        audit.proposed({'action':'offer','price':fallback},source='fallback',reason='model time budget unavailable')
        return fallback
    prompt = {"phase": "buyer_counter", "sku": sku, "list_price": list_price,
              "seller_message": message[:350], "last_offer": last_offer,
              "maximum_authorized_price": cap, "recent_memory": memory,
              'recent_turns':[{'speaker':r['speaker'],'text':text(r['text'],180)}
                              for r in (history or [])[-MAX_TURNS:]],
              "objective": "Choose the next offer or walk away. Optimize normalized Saved, not absolute currency savings. "
                           "A usable trade contributes (list_price-paid)/list_price; qualify shelf life, grade and warranty. "
                           "A quoted discount is not a realized score; broken or unusable goods can reduce Saved. "
                           "Use the suggested maximum in price_guidance when present. A higher price needs "
                           "current quality or availability evidence and must stay within the authorized cap. "
                           "Balance a better rate with completing a valid issued order before its deadline. "
                           "A high asking price is negotiable; do not reveal the private mandate limit."}
    prompt['offers_remaining']=offers_remaining
    prompt['saved_if_usable']={'at_last_offer':saved_at_price(list_price,last_offer),
                               'at_cap':saved_at_price(list_price,cap),
                               'at_seller_ask':saved_at_price(list_price,ask) if ask is not None else None,
                               'note':'Conditional usable-deal contributions, not realized final Saved.'}
    prompt['seconds_until_deadline']=max(0,round(deadline-time.monotonic()))
    schema = {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["offer", "walk"]},
        "price": {"type": ["integer", "null"]}, "reason": {"type": "string"},
    }, "required": ["action", "price", "reason"], "additionalProperties": False}
    try:
        choice = decide_json("buyer negotiator", prompt, schema)
        audit.proposed({'action':choice['action'],'price':choice.get('price')},reason=choice.get('reason'))
        if choice["action"] == "walk":
            return None
        proposed = choice.get("price")
        if type(proposed) is int:
            guarded = min(cap, max(1, proposed))
            if guarded != proposed:
                audit.guard('authorized_price_range',proposed_price=proposed,effective_price=guarded)
            trace("buyer_counter_choice", sku=sku, proposed=proposed, effective=guarded,
                  reason=choice["reason"])
            if guarded > last_offer or (ask is not None and guarded == ask):
                return guarded
            audit.guard('nonprogressing_offer',proposed_price=guarded,effective_price=fallback)
            return fallback
        audit.guard('invalid_offer_type')
    except Exception as error:
        trace("buyer_counter_fallback", error=type(error).__name__)
        audit.guard('counter_fallback',error=type(error).__name__)
        audit.proposed({'action':'offer','price':fallback},source='fallback',reason=type(error).__name__)
    return fallback


def negotiate(shop_url: str, sku: str, name: str, order: dict, *, opening_offer: int,
              cap: int, list_price: int, memory: dict, deadline: float,on_message=None,
              buyer_name: str | None = None,seller_name: str | None = None) -> dict:
    """Return a received receipt and final offer, or a concrete failure reason."""
    offered: list[int] = []
    transcript: list[dict] = []
    key=buyer_identity.load_key() if buyer_name and seller_name else None
    identity={'key':key,'name':buyer_name,'recipient':seller_name} if key is not None else None
    def remember(speaker,message,task_id=None):
        row={'speaker':speaker,'text':message,'task_id':task_id}
        transcript.append(row)
        if on_message is not None:
            on_message(dict(row))
    with httpx.Client() as client:
        endpoint = card_endpoint(client, shop_url, deadline)
        opening = opening_request(name, sku, order)
        remember('buyer',opening)
        task = send(client, endpoint, opening, deadline,identity=identity)
        audit.bind(task_id=task.get('id'))
        for turn in range(3):
            if status(task) != "TASK_STATE_INPUT_REQUIRED":
                break
            message = status_text(task)
            remember('seller',message,task.get('id'))
            ask = asking_price(message)
            if turn == 0:
                offer = min(opening_offer, ask) if ask is not None and ask <= opening_offer else opening_offer
                if offer != opening_offer:
                    audit.guard('seller_ask_below_opening_offer',selected_offer=opening_offer,effective_price=offer)
            else:
                offer = followup_offer(message=message, last_offer=offered[-1], cap=cap,
                                       sku=sku, list_price=list_price, memory=memory, deadline=deadline,
                                       history=transcript,
                                       offers_remaining=3-turn)
            if offer is None or deadline - time.monotonic() < 5:
                if offer is not None:
                    audit.guard('order_deadline')
                audit.executed('walk',status='applied',reason='walked away or order deadline reached')
                return {"receipt": None, "reason": "walked away or order deadline reached",
                        "offers": offered, "task_id": task.get("id"),'transcript':transcript}
            offer = min(cap, max(1, int(offer)))
            offered.append(offer)
            remember('buyer',str(offer),task.get('id'))
            trace("buyer_offer", sku=sku, shop=shop_url, offer=offer, turn=turn)
            task = send(client, endpoint, str(offer), deadline, task.get("id"),identity=identity)
            audit.executed('offer',price=offer,turn=turn)
        receipt = get_receipt(task) if status(task) == "TASK_STATE_COMPLETED" else None
        remember('seller',status_text(task),task.get('id'))
        return {"receipt": receipt, "reason": "receipt received" if receipt else
                f"no receipt; seller state {status(task)}", "offers": offered,
                "task_id": task.get("id"), "last_seller_message": status_text(task)[:250],
                'transcript':transcript}
