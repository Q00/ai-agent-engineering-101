"""Consume one explicit integrity audit, attached to a real kit-issued order."""
import json
from pathlib import Path
import time
import uuid


def take_audit_request(path, order, *, now=None):
    path = Path(path)
    if not path.exists():
        return None
    request = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(request.get("shop"), str) or not request["shop"]:
        raise ValueError("invalid_audit_shop")
    now = time.time() if now is None else now
    if now > request["expires_epoch"]:
        return None
    minimum = request["minimum_budgets"].get(order["item"])
    if minimum is None or order["budget"] < minimum or order.get("round", 0) < 2:
        return None
    # Renaming is the single consumption point; retries cannot audit again.
    path.rename(path.with_name("audit-consumed-" + uuid.uuid4().hex + ".json"))
    return request["shop"]
