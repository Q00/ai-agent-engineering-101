import argparse
import base64
import json
import os
import re
import time
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_data_parts, get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from memory_manager import MemoryManager
from team_memory import TeamMemory

NAME = "Jaehoonang"
MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))

def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

def sign(receipt):
    key = serialization.load_pem_private_key(Path("shop_key.pem").read_bytes(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    return dict(receipt, sig_seller=base64.b64encode(key.sign(canon(body))).decode())

LEDGER_PATH = Path(__file__).parent / "futures_ledger.json"
FWD_RE = re.compile(r"FWD-[0-9a-f]{8}")
VOUCHER_PATH = Path(__file__).parent / "loyalty_ledger.json"
VCH_RE = re.compile(r"VCH-[0-9a-f]{8}")
LOAN_LEDGER_PATH = Path(__file__).parent / "loan_ledger.json"
LOAN_RE = re.compile(r"LOAN-[0-9a-f]{8}")
REPO_LEDGER_PATH = Path(__file__).parent / "repo_ledger.json"
REPO_RE = re.compile(r"REPO-[0-9a-f]{8}")


def _anchor_config():
    reserve_ratio, anchor_sku = 0.40, "Gea072173"
    strat_path = Path("strategy.json")
    if strat_path.exists():
        try:
            mono = json.loads(strat_path.read_text(encoding="utf-8")).get("monopoly_supplier", {})
            reserve_ratio = mono.get("reserve_target_ratio", reserve_ratio)
            anchor_sku = mono.get("commodity_anchor_sku", anchor_sku)
        except Exception:
            pass
    return reserve_ratio, anchor_sku

def find_anchor_unit(taken=()):
    """The monopolized SKU is reserve backing for the loan desk and forward contracts (see
    loan_engine.py / futures_engine.py), not a casual walk-up item -- it is deliberately kept out of
    find_unit()'s general text matching and only reachable through those two structured, coded
    channels. Selling it to anyone who happens to type 'knife' would also contradict the scarcity
    claims we post to the forum."""
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    reserve_ratio, anchor_sku = _anchor_config()
    anchor_units = [u for u in inv.get("units", []) if u.get("sku") == anchor_sku
                    and u.get("grade") != "expired" and u.get("unit") not in taken]
    reserve_limit = int(len(anchor_units) * reserve_ratio)
    sellable = anchor_units[reserve_limit:] if len(anchor_units) > reserve_limit else []
    return min(sellable, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at", 0)), default=None)

_MATCH_STOPWORDS = {"i", "want", "to", "buy", "a", "an", "the", "for", "please"}


def _unit_matches(u, want):
    """True if the buyer's query text and this unit's category/name/SKU share a real match, checked
    BOTH directions. 2026-11-14 bug found live: the original check was `category in want` / `name in
    want` -- only ever true when the BUYER's text is longer than and contains our full category
    segment or product name. Confirmed live: we hold 6 units of '[신지모루] 저소음 무드등 미니 가습기
    간편세척 대용량 초음파 사무실 책상' (category last-segment '냉방/환기/제습'), and a standard
    "I want to buy a 가습기" query -- the exact format market_buyer.py and (per the shared starter
    kit) most other buyers use -- got 'Out of stock or no matching inventory.' even though the word
    '가습기' sits right in our product name. Only the anchor SKU's short category segment ('나이프')
    was ever short enough to coincidentally match this way, which is why it was the one SKU that sold.

    First fix attempt added `want in category` / `want in name` (plain bidirectional containment) --
    still failed the live repro: `want` is the FULL sentence "i want to buy a 가습기", which does not
    equal or get contained by either direction against a multi-word name either (the sentence isn't a
    substring of the name, and the name isn't a substring of the sentence). The real fix needs to
    pull the keyword OUT of the boilerplate sentence: split `want` into words, drop common request
    boilerplate, and check whether any remaining word is a substring of our category/name -- this is
    what actually catches '가습기' inside the longer product name."""
    cat = (u.get("category") or "").split(">")[-1].lower()
    name = (u.get("name") or "").lower()
    sku = u["sku"].lower()
    if (sku in want or want in sku) or (cat and (cat in want or want in cat)) or (name and (name in want or want in name)):
        return True
    tokens = [t for t in want.split() if len(t) >= 2 and t not in _MATCH_STOPWORDS]
    return any(t in cat or t in name for t in tokens)


def find_unit(text, taken=()):
    """General text/category/SKU matching, non-anchor stock first. Falls back to anchor-SKU
    surplus ABOVE the reserve floor (same floor find_anchor_unit protects) when nothing else
    matched -- otherwise that surplus is a dead end: find_anchor_unit only ever runs when the
    buyer's message already carries a forward/loan/repo code, so 2,650 anchor units (2.47M won of
    cost) sat completely unreachable by ordinary demand even though 60% of them (above the 40%
    reserve) were meant to be sellable. PricingTeam.price_unit already prices a non-captive anchor
    sale at liquidation_markup_ratio (near cost) -- that branch existed but was dead code because
    nothing ever routed a normal buyer into it. The reserve floor itself is untouched: only the
    surplus above it is exposed, and only when the buyer's own words actually name this SKU,
    its category, or its name -- never a silent substitution for whatever they really asked for."""
    want = text.lower()
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    reserve_ratio, anchor_sku = _anchor_config()
    other_units = [u for u in inv.get("units", []) if u.get("sku") != anchor_sku
                   and u.get("grade") != "expired" and u.get("unit") not in taken]
    fit = [u for u in other_units if _unit_matches(u, want)]
    named = [u for u in fit if u["sku"].lower() in want]
    fit = named or fit
    if fit:
        return min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at", 0)))
    anchor_units = [u for u in inv.get("units", []) if u.get("sku") == anchor_sku
                    and u.get("grade") != "expired" and u.get("unit") not in taken]
    reserve_limit = int(len(anchor_units) * reserve_ratio)
    surplus = anchor_units[reserve_limit:] if len(anchor_units) > reserve_limit else []
    anchor_fit = [u for u in surplus if _unit_matches(u, want)]
    return min(anchor_fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at", 0)),
               default=None)


class PricingTeam:
    """판매전담부 - 가격팀 (Pricing Team): 전략 설정을 읽어 원가 대비 마크업, 선도계약(FWD) 고정가,
    바우처 할인을 적용한 최종 판매가를 계산한다. 가격 결정 외의 책임(고객 인식, 교차판매)은 다루지
    않는다 -- 단일 책임을 지켜 각 팀이 독립적으로 검증/수정 가능하도록 분리."""

    def __init__(self):
        self.memory = TeamMemory("pricing_team", Path(__file__).parent)

    def load_strategy(self):
        strat_path = Path("strategy.json")
        if strat_path.exists():
            try:
                return json.loads(strat_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _claim_forward_contract(self, text, sku):
        """If the buyer's message references a live, unclaimed forward contract (see
        futures_engine.py) for this SKU, claim it and return its locked strike price. Mutates
        futures_ledger.json so the same contract cannot be claimed twice."""
        m = FWD_RE.search(text or "")
        if not m or not LEDGER_PATH.exists():
            return None
        try:
            ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        now = time.time()
        for c in ledger.get("contracts", []):
            if c["id"] == m.group(0) and c["sku"] == sku and not c["claimed"] and c["expires_at"] > now:
                c["claimed"] = True
                tmp = LEDGER_PATH.with_suffix(".tmp")
                tmp.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(LEDGER_PATH)
                return c["strike_price"]
        return None

    def _redeem_voucher(self, text, sku, price):
        """Apply a live, unredeemed voucher referenced in the buyer's message as a discount off
        price for this SKU. Never discounts below cost+10 (enforced by the caller). Returns the
        voucher's customer_id too -- the chain identifier LoyaltyTeam uses to recognize a returning
        buyer, since the A2A protocol itself never reveals who is messaging us."""
        m = VCH_RE.search(text or "")
        if not m:
            return price, None, None
        data = LoyaltyTeam.load_vouchers()
        now = time.time()
        for v in data.get("vouchers", []):
            if v["id"] == m.group(0) and v["sku"] == sku and not v["redeemed"] and v["expires_at"] > now:
                v["redeemed"] = True
                LoyaltyTeam.save_vouchers(data)
                return max(price - v["value"], 0), v["value"], v.get("customer_id", v["id"])
        return price, None, None

    def _redeem_repo_token(self, text, sku, anchor_sku, price):
        """Redeem a REPO-xxxxxxxx JAE-Credit token (see repo_engine.py) as a discount, for ANY
        non-anchor item -- unlike a loyalty voucher, this is a bearer instrument not tied to a
        specific SKU or to the shop/buyer it was originally issued to: whoever holds the code can
        spend it, since the market tracks no ownership of it. The anchor SKU is excluded here
        because a REPO code on an anchor purchase means repayment (see CrossSellTeam), not spend."""
        if sku == anchor_sku:
            return price, None
        m = REPO_RE.search(text or "")
        if not m or not REPO_LEDGER_PATH.exists():
            return price, None
        try:
            data = json.loads(REPO_LEDGER_PATH.read_text(encoding="utf-8"))
        except Exception:
            return price, None
        for r in data.get("repos", []):
            if r["id"] == m.group(0) and r["status"] == "disbursed" and r.get("token_remaining", 0) > 0:
                spend = min(price, r["token_remaining"])
                r["token_remaining"] -= spend
                tmp = REPO_LEDGER_PATH.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(REPO_LEDGER_PATH)
                self.memory.bump("repo_tokens_spent")
                return max(price - spend, 0), spend
        return price, None

    def price_unit(self, unit, text, st):
        """Returns a dict with the final price and every signal downstream teams/text need:
        locked_price, voucher_used, redeemed_customer_id, markup, cost, anchor_sku."""
        mono = st.get("monopoly_supplier", {})
        anchor_sku = mono.get("commodity_anchor_sku")
        markup = 1.40
        is_loan_repayment = unit.get("sku") == anchor_sku and (
            LOAN_RE.search(text or "") is not None or REPO_RE.search(text or "") is not None
        )
        try:
            base_markup = mono.get("monopoly_markup_ratio", 1.40)
            if is_loan_repayment:
                # A loan/repo-repayment buyer is captive: the lending terms require THEM to buy
                # THIS SKU from US to clear debt, so we have maximum pricing power here, not minimum.
                # Selling at the same near-cost liquidation price as a casual clearance buyer would
                # mean a won of debt translates into far more physical units leaving a reserve we
                # publicly advertise as "90% depleted" on the forum -- undermining our own scarcity
                # story and emptying the shelf faster than necessary for a transaction that is not
                # itself a profitable sale.
                markup = mono.get("loan_repayment_markup_ratio", 3.0)
            elif unit.get("sku") == anchor_sku:
                # 2026-11-11 (explicit user decision): there is no real platform-enforced monopoly
                # on this SKU -- any shop can restock the identical catalog item at any time (no
                # shared/exhaustible supply, see REPORT.md 분석), and it has sold only ~17 times ever
                # under the old near-cost liquidation framing. Stop pricing it as a fire-sale glut;
                # price it the same as any other item we actually have pricing power on (premium),
                # and let dutch_auction_decay (below) pull the price down over time if it still
                # doesn't move -- a real market-clearing price instead of an artificial fixed floor.
                markup = mono.get("premium_markup_ratio", base_markup)
            else:
                markup = mono.get("premium_markup_ratio", base_markup)
        except Exception:
            pass

        cost = unit["cost"]
        list_price = unit.get("list_price") or 0
        locked_price = self._claim_forward_contract(text, unit["sku"])
        is_anchor = unit.get("sku") == anchor_sku
        # Fixed peg now applies ONLY to loan/repo repayment (a deliberate settlement exchange-rate,
        # unrelated to market value -- see the comment below). Regular/non-captive anchor sales no
        # longer peg to a fixed liquidation price; they fall through to the normal markup formula
        # below (now premium_markup_ratio, see the markup branch above) so price reflects real value
        # and responds to dutch_auction_decay like every other SKU.
        anchor_fixed_price = mono.get("anchor_loan_repayment_fixed_price") if (is_anchor and is_loan_repayment) else None

        if locked_price is not None:
            price = locked_price
        elif anchor_fixed_price is not None:
            # Fixed exchange rate ("배터리본위제" -- the anchor SKU doubles as the unit of account
            # for loan/repo repayment). Without this, repayment price swung 1,188~3,552원 (3x) purely
            # from each unit's randomly-drawn cost (396~1,184 across our 2,650 held units) -- the same
            # nominal debt repaid in the same SKU could cost a borrower 3x more or less by pure luck,
            # defeating the point of using it as a settlement peg. Deliberately bypasses the
            # competitive_price_cap_ratio/dutch-decay logic below -- those exist to keep diversified
            # trade-good pricing competitive, not to move a deliberately fixed reference price.
            # CAVEAT: the final `price = max(price, cost + 10)` safety net below still applies after
            # this -- a configured peg at or below the max possible per-unit cost+10 (list_price x
            # 0.75 + 10, e.g. 1,195 for this anchor SKU at list 1,580) silently stops being fixed for
            # the priciest draws. Keep the peg comfortably above that ceiling.
            price = int(anchor_fixed_price)
        else:
            price = int(cost * markup)
            # Cost is drawn randomly per unit (A-grade draws land at 55-75% of list), so a flat
            # markup on cost alone can price us above list. Forum price-report posts show
            # competitors settling near 72% of list for this category -- the cap used to sit at 80%,
            # which this markup almost always hit (cost*1.5 exceeds 80% of list whenever cost is
            # above ~53% of list, which is most of the cost range), meaning most of our non-anchor
            # stock was quoted ~8 points of list price ABOVE the going competitor rate. Undercut it
            # instead of merely matching it.
            if list_price and not is_loan_repayment:
                cap_ratio = mono.get("competitive_price_cap_ratio", 0.68)
                price = min(price, int(list_price * cap_ratio))
            price = max(price, cost + 10)
            # Dutch-auction decay (strategy.json dutch_auction_decay -- previously declared but never
            # read by any code, confirmed in audit). A captive loan/repo-repayment buyer must still
            # pay full pricing power (markup logic above already exists for that reason), so decay is
            # skipped there; everything else that sits unsold past age_days_trigger loses
            # hourly_discount_rate per hour on top of its already-computed price, floored at
            # hard_floor_ratio x cost so "원가 이하 판매 절대 거절" (REPORT.md 2.9) still holds. Goods
            # also pay 1%/day storage while they sit (rules.md), so moving aged stock even at thin
            # margin beats paying storage on it indefinitely.
            if not is_loan_repayment and unit.get("supplied_at"):
                decay = st.get("dutch_auction_decay", {})
                trigger_hours = decay.get("age_days_trigger", 1) * 24
                age_hours = (time.time() - unit["supplied_at"]) / 3600
                if age_hours > trigger_hours:
                    hourly_rate = decay.get("hourly_discount_rate", 0.02)
                    hard_floor_ratio = decay.get("hard_floor_ratio", 1.01)
                    discount = min(hourly_rate * (age_hours - trigger_hours), 0.9)
                    price = max(int(price * (1 - discount)), int(cost * hard_floor_ratio))
        price, voucher_used, redeemed_customer_id = self._redeem_voucher(text, unit["sku"], price)
        price, repo_token_spent = self._redeem_repo_token(text, unit["sku"], anchor_sku, price)
        price = max(price, cost + 10)
        self.memory.bump("prices_quoted")
        return {
            "price": price, "cost": cost, "markup": markup, "anchor_sku": anchor_sku,
            "locked_price": locked_price, "voucher_used": voucher_used,
            "redeemed_customer_id": redeemed_customer_id, "repo_token_spent": repo_token_spent,
        }


class LoyaltyTeam:
    """판매전담부 - 고객관리팀 (Loyalty Team): 신규 고객 환영 할인, 재구매 바우처 발급, 단골 고객
    인식을 담당한다. 고객 식별은 memory_manager.MemoryManager(자체 메모리 memory_store.json)가 추적하는
    바우처 체인 ID로 이뤄진다 -- A2A 메시지에는 발신자 신원이 없기 때문."""

    def __init__(self):
        self.mm = MemoryManager()
        self.memory = TeamMemory("loyalty_team", Path(__file__).parent)

    @staticmethod
    def load_vouchers():
        if VOUCHER_PATH.exists():
            try:
                return json.loads(VOUCHER_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"vouchers": []}

    @staticmethod
    def save_vouchers(data):
        tmp = VOUCHER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(VOUCHER_PATH)

    def apply_welcome_discount(self, price_info, cost, st):
        """First-touch acquisition incentive: nobody presenting a voucher chain, forward lock, or
        loan-repayment code is -- by construction -- a buyer we have no record of yet. Give them a
        welcome discount off the premium (non-anchor) stock to seed a relationship; the anchor SKU
        is already at near-cost liquidation markup so stacking a discount there risks a loss."""
        if not (price_info["redeemed_customer_id"] is None and price_info["locked_price"] is None):
            return None, price_info["price"]
        new_rate = st.get("loyalty_program", {}).get("new_customer_discount_rate", 0.05)
        discount = int(price_info["price"] * new_rate)
        price = max(price_info["price"] - discount, cost + 10)
        self.memory.bump("welcome_discounts_given")
        return discount, price

    def issue_voucher(self, sku, sale_price, st, customer_id=None):
        """After a sale, mint a voucher worth voucher_rate of the sale toward the buyer's next
        purchase of this SKU -- a repeat-purchase incentive since there is no server primitive to
        hand out cash or credit directly. customer_id continues an existing redemption chain (same
        returning buyer) or, if None, starts a brand new chain anchored on this voucher's own id."""
        loyalty = st.get("loyalty_program", {})
        if not loyalty.get("enabled"):
            return None
        data = self.load_vouchers()
        vid = f"VCH-{base64.b16encode(os.urandom(4)).decode().lower()}"
        voucher = {
            "id": vid,
            "sku": sku,
            "value": max(int(sale_price * loyalty.get("voucher_rate", 0.2)), 1),
            "issued_at": time.time(),
            "expires_at": time.time() + loyalty.get("voucher_lifetime_sec", 86400),
            "redeemed": False,
            "customer_id": customer_id or vid,
        }
        data.setdefault("vouchers", []).append(voucher)
        self.save_vouchers(data)
        self.memory.bump("vouchers_issued")
        return voucher

    def issue_overpayment_credit(self, sku, value, st):
        """Refund the exact won amount a loan/repo repayment overshot amount_due by (see
        mark_loan_repaid: unit prices rarely divide the debt evenly, so the qualifying purchase
        almost always pays slightly more than owed). This mints a voucher for the EXACT overshoot
        value, not a rate-scaled incentive, and always fires regardless of loyalty_program.enabled
        -- it is a refund of real overpaid money, not a marketing perk."""
        if value <= 0:
            return None
        data = self.load_vouchers()
        vid = f"VCH-{base64.b16encode(os.urandom(4)).decode().lower()}"
        voucher = {
            "id": vid,
            "sku": sku,
            "value": value,
            "issued_at": time.time(),
            "expires_at": time.time() + st.get("loyalty_program", {}).get("voucher_lifetime_sec", 3600),
            "redeemed": False,
            "customer_id": vid,
        }
        data.setdefault("vouchers", []).append(voucher)
        self.save_vouchers(data)
        self.memory.bump("overpayment_credits_issued")
        return voucher

    def track_customer(self, customer_id, sku, price):
        """Persist this sale against the buyer's voucher-chain identity and report whether they are
        a recognized repeat customer plus their preferred SKU, if any."""
        if not customer_id:
            return False, None
        is_repeat = self.mm.is_repeat_customer(customer_id)
        preferred_sku = self.mm.customer_preferred_sku(customer_id)
        self.mm.record_customer_activity(customer_id, sku, price)
        if is_repeat:
            self.memory.bump("repeat_customers_recognized")
        return is_repeat, preferred_sku


class CrossSellTeam:
    """판매전담부 - 교차판매팀 (Cross-sell Team): 대출 상환 코드를 판매 시점에 인식해 대출팀(loan_engine.py)
    장부에 반영하고, 모든 영수증에 대출 상품을 한국어로 교차판매한다 -- 포럼 광고만으로는 부족하다고
    판단해 실제 거래가 일어나는 바로 그 순간, 거래 상대에게 직접 대출을 제안한다."""

    def __init__(self):
        self.memory = TeamMemory("crosssell_team", Path(__file__).parent)

    def mark_loan_repaid(self, text, sku, price, st):
        """Repayment leg of the loan desk (see loan_engine.py): a disbursed loan referenced by its
        LOAN-id in a purchase of the monopolized SKU has this purchase's price added to its
        cumulative repaid_amount. Only once that total reaches amount_due (principal + the
        tier-based interest loan_engine.py priced at request time) is the loan marked repaid -- a
        single touch used to mark the whole loan repaid regardless of amount, which meant interest
        was never actually collected. Unit prices rarely divide amount_due evenly (random per-unit
        cost means the final qualifying purchase almost always overshoots it by some remainder) --
        that remainder is reported back as `overshoot` so the caller can refund it as usable credit
        instead of silently keeping value the borrower did not actually owe.
        Also accepts a loan already marked "defaulted" -- the public alert/credit downgrade punish
        the default, but a borrower who later shows up to pay their debt in full must still have a
        way to actually clear it (confirmed bug: the check used to require status=="disbursed" only,
        so once defaulted a loan could never be repaid again, no matter what the borrower did)."""
        anchor_sku = st.get("monopoly_supplier", {}).get("commodity_anchor_sku")
        if sku != anchor_sku or not LOAN_LEDGER_PATH.exists():
            return None
        m = LOAN_RE.search(text or "")
        if not m:
            return None
        try:
            data = json.loads(LOAN_LEDGER_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        for l in data.get("loans", []):
            if l["id"] == m.group(0) and l["status"] in ("disbursed", "defaulted"):
                was_defaulted = l["status"] == "defaulted"
                due = l.get("amount_due") or l.get("amount", 0)
                l["repaid_amount"] = l.get("repaid_amount", 0) + price
                full = l["repaid_amount"] >= due
                overshoot = max(l["repaid_amount"] - due, 0) if full else 0
                if full:
                    l["status"] = "repaid"
                tmp = LOAN_LEDGER_PATH.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(LOAN_LEDGER_PATH)
                self.memory.bump("loans_repaid_at_checkout" if full else "partial_repayments_at_checkout")
                if was_defaulted:
                    self.memory.bump("late_payments_after_default")
                return {"id": l["id"], "full": full, "repaid_amount": l["repaid_amount"], "amount_due": due,
                        "remaining": max(due - l["repaid_amount"], 0), "overshoot": overshoot,
                        "was_defaulted": was_defaulted}
        return None

    def mark_repo_repaid(self, text, sku, price, st):
        """Repayment leg of a repo token (see repo_engine.py): the borrower who originally received
        a REPO-xxxxxxxx token still owes amount_due regardless of who else has spent the token as a
        discount elsewhere -- repayment and token spend are tracked independently on the same
        record. Anchor SKU only, mirroring the loan desk's repayment mechanic (including the
        overshoot remainder -- see mark_loan_repaid)."""
        anchor_sku = st.get("monopoly_supplier", {}).get("commodity_anchor_sku")
        if sku != anchor_sku or not REPO_LEDGER_PATH.exists():
            return None
        m = REPO_RE.search(text or "")
        if not m:
            return None
        try:
            data = json.loads(REPO_LEDGER_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        for r in data.get("repos", []):
            if r["id"] == m.group(0) and r["status"] == "disbursed":
                due = r.get("amount_due") or r.get("amount", 0)
                r["repaid_amount"] = r.get("repaid_amount", 0) + price
                full = r["repaid_amount"] >= due
                overshoot = max(r["repaid_amount"] - due, 0) if full else 0
                if full:
                    r["status"] = "repaid"
                tmp = REPO_LEDGER_PATH.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(REPO_LEDGER_PATH)
                self.memory.bump("repos_repaid_at_checkout" if full else "partial_repo_repayments_at_checkout")
                return {"id": r["id"], "full": full, "repaid_amount": r["repaid_amount"], "amount_due": due,
                        "remaining": max(due - r["repaid_amount"], 0), "overshoot": overshoot}
        return None

    def pitch(self, anchor_sku):
        """Korean loan cross-sell line appended to every receipt."""
        self.memory.bump("loan_pitches_shown")
        sku_txt = anchor_sku or "저희 기축 상품"
        return (f" 💰 본인 상점을 운영 중이고 자금이 필요하신가요? 이 포럼에 \"[LOAN REQUEST] <금액>\"을 올리시면 "
                f"신용등급별 차등 금리(AAA 3%~B 18%)로 대출을 받으실 수 있습니다 (담보 없음, SKU {sku_txt} 구매로 상환).")

    def futures_pitch(self):
        """선물계약(futures_engine.py)은 포럼에만 공시되고, 대부분의 구매자 하네스는 포럼을 읽지 않고
        "I want to buy X" 한 줄만 보내는 단발성 협상을 쓰기 때문에(see market_buyer.py: negotiate()가
        첫 응답이 완료 상태면 두 번째 라운드를 보내지 않음) 체결로 이어지기가 구조적으로 어렵다. 지금
        실제로 우리와 대화하고 있는 방금 구매자에게 영수증에서 직접 알려주면, 적어도 우리 실제 구매자
        층에게는 발견 가능성이 생긴다 -- 포럼을 전혀 안 읽어도 되는 유일한 채널."""
        try:
            ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8")) if LEDGER_PATH.exists() else {}
        except Exception:
            return ""
        now = time.time()
        open_contracts = [c for c in ledger.get("contracts", [])
                           if not c["claimed"] and c.get("expires_at", 0) > now]
        if not open_contracts:
            return ""
        c = min(open_contracts, key=lambda x: x["expires_at"])
        mins_left = max(int((c["expires_at"] - now) / 60), 0)
        self.memory.bump("futures_pitches_shown")
        return (f" 📈 선도계약 {c['id']}: SKU {c['sku']}를 {c['strike_price']:,}원에 고정 -- {mins_left}분 내 "
                f"\"{c['id']}\" 포함해서 저희 상점에 다시 메시지하시면 이후 가격 변동과 무관하게 이 가격에 "
                f"구매 확정됩니다.")


pricing_team = PricingTeam()
loyalty_team = LoyaltyTeam()
crosssell_team = CrossSellTeam()


class Seller(AgentExecutor):
    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        text = get_message_text(context.message) or ""
        taken = [d["unit"] for art in (task.artifacts or []) for d in get_data_parts(art.parts)
                 if isinstance(d, dict) and d.get("unit")] if task else []
        # the monopolized SKU is reserve backing, reachable only via a forward-contract or loan/repo
        # repayment code -- never by casually asking for "knife"/"battery"/the raw SKU. A REPO code
        # is dual-purpose (repayment on the anchor SKU, or a spendable discount on anything else),
        # so it only routes to the anchor SKU when the message also names it -- otherwise it falls
        # through to normal item matching and the token applies as a discount there instead.
        _, anchor_sku_hint = _anchor_config()
        wants_anchor_via_repo = bool(REPO_RE.search(text) and anchor_sku_hint.lower() in text.lower())
        unit = find_anchor_unit(taken) if (FWD_RE.search(text) or LOAN_RE.search(text) or wants_anchor_via_repo) else find_unit(text, taken)

        if not unit:
            updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
            await updater.add_artifact([new_text_part("Out of stock or no matching inventory.")])
            await updater.complete()
            return

        st = pricing_team.load_strategy()
        price_info = pricing_team.price_unit(unit, text, st)
        price, cost, markup, anchor_sku = price_info["price"], price_info["cost"], price_info["markup"], price_info["anchor_sku"]
        locked_price, voucher_used = price_info["locked_price"], price_info["voucher_used"]
        redeemed_customer_id = price_info["redeemed_customer_id"]
        repo_token_spent = price_info["repo_token_spent"]

        welcome_discount = None
        if unit.get("sku") != anchor_sku:
            welcome_discount, price = loyalty_team.apply_welcome_discount(price_info, cost, st)

        warranty_hours = st.get("signaling_warranty_hours", 1)
        # claim/cert: unit["grade"] is the unit's real, ledger-verified grade (A/B/C) and unit["cert"]
        # is its signed supply certificate -- both already exist on every unit but were never put on
        # the receipt, so market_kit.py's ACP layer (which reads them from this exact dict via
        # self.acp.terms[task]) always shipped checkouts with no claim and no certificate attached.
        # Every other top shop's claims/honest_claims board counters run near 1:1 (honest); ours sat
        # at 0/0 because we simply never made a claim. Claiming the unit's own true grade here can
        # never be dishonest -- it is not a new risk, just a previously-missing truthful signal.
        receipt = sign({
            "shop": NAME,
            "item": unit["sku"],
            "unit": unit["unit"],
            "price": price,
            "task": task.id,
            "warranty_hours": warranty_hours,
            "claim": unit.get("grade") if unit.get("grade") in ("A", "B", "C") else None,
            "cert": unit.get("cert"),
        })

        loyalty_team.mm.update_sku_velocity(unit["sku"], price)
        new_voucher = loyalty_team.issue_voucher(unit["sku"], price, st, customer_id=redeemed_customer_id)
        repaid_loan = crosssell_team.mark_loan_repaid(text, unit["sku"], price, st)
        repaid_repo = crosssell_team.mark_repo_repaid(text, unit["sku"], price, st)
        overshoot_credit = None
        if repaid_loan and repaid_loan.get("overshoot"):
            overshoot_credit = loyalty_team.issue_overpayment_credit(unit["sku"], repaid_loan["overshoot"], st)
        elif repaid_repo and repaid_repo.get("overshoot"):
            overshoot_credit = loyalty_team.issue_overpayment_credit(unit["sku"], repaid_repo["overshoot"], st)

        tracked_customer_id = redeemed_customer_id or (new_voucher["customer_id"] if new_voucher else None)
        is_repeat_customer, preferred_sku = loyalty_team.track_customer(tracked_customer_id, unit["sku"], price)

        if locked_price is not None:
            note = "locked forward contract price"
        elif voucher_used is not None:
            note = f"voucher {voucher_used} won discount applied"
        elif repo_token_spent is not None:
            note = f"REPO credit token {repo_token_spent} won discount applied"
        elif welcome_discount:
            note = f"first-time buyer discount: {welcome_discount} won off"
        else:
            note = f"Cost: {cost}, Markup: {markup}x"
        text_out = f"Offering {unit['name']} (SKU: {unit['sku']}, grade {unit.get('grade')}) for {price} ({note}). {warranty_hours}h warranty. Signed with Ed25519."
        if new_voucher:
            valid_min = int((new_voucher["expires_at"] - new_voucher["issued_at"]) / 60)
            text_out += (f" You earned voucher {new_voucher['id']} worth {new_voucher['value']} won off your "
                         f"next {unit['sku']} purchase, valid {valid_min} min.")
        if repaid_loan:
            late_tag = " (연체 이후 상환)" if repaid_loan.get("was_defaulted") else ""
            if repaid_loan["full"]:
                text_out += (f" {repaid_loan['id']} 상환 완료{late_tag} (누적 {repaid_loan['repaid_amount']:,}/"
                             f"{repaid_loan['amount_due']:,}원). 신용등급 회복.")
            else:
                text_out += (f" {repaid_loan['id']} 일부 상환 접수{late_tag} (누적 {repaid_loan['repaid_amount']:,}/"
                             f"{repaid_loan['amount_due']:,}원, 잔액 {repaid_loan['remaining']:,}원 추가 상환 필요).")
        if repaid_repo:
            if repaid_repo["full"]:
                text_out += (f" {repaid_repo['id']} 대차거래 상환 완료 (누적 {repaid_repo['repaid_amount']:,}/"
                             f"{repaid_repo['amount_due']:,}원). 신용등급 회복.")
            else:
                text_out += (f" {repaid_repo['id']} 대차거래 일부 상환 접수 (누적 {repaid_repo['repaid_amount']:,}/"
                             f"{repaid_repo['amount_due']:,}원, 잔액 {repaid_repo['remaining']:,}원 추가 상환 필요).")
        if overshoot_credit:
            text_out += (f" 상환액이 {overshoot_credit['value']:,}원 초과 지급되어 바우처 {overshoot_credit['id']}로 "
                         f"돌려드립니다 (단위 가격이 상환필요액을 정확히 나누지 않아 생긴 차액, {unit['sku']} 구매 시 사용 가능).")
        if is_repeat_customer:
            text_out += " Welcome back -- thanks for returning to Jaehoonang."
            if preferred_sku and preferred_sku != unit["sku"]:
                text_out += f" (Still stocking {preferred_sku} if that's what you were after.)"
        elif welcome_discount:
            text_out += f" First purchase with us -- {welcome_discount} won welcome discount applied, no strings attached."
        # 대출 교차판매: 포럼 광고만으로는 부족하므로, 실제 거래가 이뤄지는 바로 이 순간 거래 상대에게
        # 직접 대출 상품을 제안한다.
        text_out += crosssell_team.pitch(anchor_sku)
        text_out += crosssell_team.futures_pitch()

        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        await updater.add_artifact([new_data_part(receipt), new_text_part(text_out)])
        await updater.complete()

    async def cancel(self, context, event_queue):
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()

def build(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Jaehoonang: real stock of certified hardware, furniture (beds, mattresses, sofas, "
                     "chairs), appliances and electronics. Every unit ships with the market's unit "
                     "certificate; grade claims are checked against the public ledger. Goods carry a "
                     "6 h warranty. Loan and repo/credit services also available.",
        version="1.0",
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(id="buy", name="Negotiate a purchase",
                       description="Ask for an item by SKU or name (hardware, furniture, appliances, "
                       "electronics). The shop quotes a price from certified stock with the unit's "
                       "grade and warranty stated. Send a counteroffer as a number, or accept the ask "
                       "-- the shop closes with a signed receipt.",
                       tags=["hardware", "furniture", "bed", "mattress", "sofa", "chair", "appliance", "electronics"]),
        ]
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    port = ap.parse_args().port
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
