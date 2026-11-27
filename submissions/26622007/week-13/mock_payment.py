# MeshKit 모의 결제 레이어 — 라우팅·정산·영수증을 하나로 묶은 최소 구현
import hashlib, hmac, json, time
from dataclasses import dataclass, field, asdict

PLATFORM_SECRET = b"meshkit-demo-key"   # 데모용. 실서비스에선 절대 하드코딩 금지
FEE_RATE = 0.03                       # 플랫폼 수수료 3%

def canonical(payload: dict) -> bytes:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return body

def digest(payload: dict) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()

def auth_tag(payload: dict) -> str:
    # 플랫폼이 만든 이벤트의 인증·무결성 태그. 부인 방지 전자서명이 아니다.
    return hmac.new(PLATFORM_SECRET, canonical(payload), hashlib.sha256).hexdigest()

class Ledger:
    # append-only 원장 — 잔고는 저장하지 않고 항목의 합으로 계산한다
    def __init__(self):
        self.entries = []                # 지워지지 않는 항목의 나열

    def post(self, txn_id, moves):
        # moves 예: {"user": -10000, "seller": 9700, "platform": 300}
        if sum(moves.values()) != 0:
            raise ValueError("장부 불균형: 항목 합이 0이 아니다")
        for account, delta in moves.items():
            self.entries.append({"txn": txn_id, "acct": account,
                                 "delta": delta, "ts": time.time()})

    def balance(self, account) -> int:
        return sum(e["delta"] for e in self.entries if e["acct"] == account)

def route_payment(signed_contract, mandate):
    # 결제 라우팅 게이트: 자율 집행 / 인간 확인 / 거절 셋 중 하나
    body = signed_contract["body"]
    amount = body["offer"]["total"]
    seller = body["parties"]["seller"]
    if seller in mandate["blocked"]:
        return "reject"                # 금지 범주
    if amount <= mandate["auto_limit"] and seller in mandate["trusted"]:
        return "auto"                  # 위험 비례 확인: 소액·신뢰 → 자동
    return "confirm"                   # 그 외는 사람에게 묻는다

def settle_and_receipt(ledger, signed_contract):
    # W7/W11 계약은 그대로 두고 contract_id/hash를 참조하는 이벤트를 추가한다.
    body = signed_contract["body"]
    if body["schema"] != "meshkit.transaction-contract/v1":
        raise ValueError("unsupported contract schema")
    if digest(body) != signed_contract["hash"]:
        raise ValueError("contract hash mismatch")
    amount = body["offer"]["total"]
    fee = round(amount * FEE_RATE)
    txn_id = body["contract_id"]
    ledger.post(txn_id, {"user": -amount,
                         "seller": amount - fee,
                         "platform": fee})
    receipt = {
        "schema": "meshkit.transaction-event/v1",
        "event": "settled",
        "contract_id": txn_id,
        "contract_hash": signed_contract["hash"],
        "amount": amount,
        "fee": fee,
        "settled_at": time.time(),
    }
    receipt["platform_auth_tag"] = auth_tag(receipt)
    return receipt
