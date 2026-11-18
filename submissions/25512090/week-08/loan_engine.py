import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx

from outbox_utils import post_to_outbox
from team_memory import TeamMemory

MARKET_DIR = Path(os.environ.get("MARKET_DIR") or (Path(__file__).resolve().parent / ".market"))
OUTBOX_PATH = MARKET_DIR / "outbox.jsonl"
FORUM_SNAPSHOT_PATH = MARKET_DIR / "forum.json"
LOAN_LEDGER_PATH = Path(__file__).parent / "loan_ledger.json"
LOAN_ATTEMPTS_PATH = Path(__file__).parent / "loan_attempts.json"  # written by market_buyer.py: purchase attempts against a pending borrower that did not disburse
RATINGS_PATH = Path(__file__).parent / "credit_ratings.json"
SHOP_NAME = "Jaehoonang"
MARKET_API_BASE = os.environ.get("MARKET_API_BASE", "https://market.ourolabs.ai")
TOTAL_LOAN_EXPOSURE_FRACTION = 0.3  # never have more than this share of (balance - safety floor) outstanding across ALL loans combined
# Risk control: confirmed in practice that a single borrower's loan could legally consume the ENTIRE
# lending pool at once (q00 alone absorbed ~100% of capacity) -- one borrower vanishing or defaulting
# would then wipe out the whole desk's risk budget in one event. No single borrower may be approved
# for more than this share of the total pool, regardless of how much capacity their own tier/stock
# would otherwise support -- forces diversification across borrowers, not just across loans.
MAX_BORROWER_POOL_SHARE = 0.6
# How much of a borrower's own public stock value we're willing to lend against, scaled by their
# ledger-verified credit tier -- a AAA borrower with a clean public record gets most of their
# apparent capacity; an unrated/unknown one gets a conservative slice.
TIER_LOAN_FRACTION = {"AAA": 0.6, "AA": 0.5, "A": 0.4, "BBB": 0.3, "B": 0.15}
TIER_LOAN_FRACTION_DEFAULT = 0.2  # no credit history with us or the market yet
MIN_MEANINGFUL_LOAN = 1000  # below this, a counter-offer isn't worth the forum post

LOAN_REQUEST_RE = "[LOAN REQUEST]"  # the convention we publish: shops ask for a loan by posting this tag
# Requires actual digits right after the tag -- a naive "scrape any digit in the next 20 chars"
# approach previously matched the "2" inside a "2️⃣" emoji codepoint in our own ad copy.
LOAN_REQUEST_AMOUNT_RE = re.compile(r"\[LOAN REQUEST\]\s*([0-9][0-9,]*)")
LOAN_DUE_SEC = 3 * 3600  # repay within 3 hours of disbursement
# 독촉장(dunning notice) 트리거: 연체 직후 바로 쏘지 않고(그 순간은 check_defaults의 사실 공시로 충분),
# 연체가 이만큼 더 길어졌을 때만 더 강한 공개 독촉장을 추가로 올린다. 온라인/오프라인 여부는 보지 않는다.
COLLECTION_NOTICE_MIN_OVERDUE_SEC = 1800  # 상환 기한 경과 후 최소 이만큼은 지나야 독촉장 대상
COLLECTION_NOTICE_INTERVAL_SEC = 300  # 연체가 유지되는 동안 재게시 주기 -- 마켓 규칙(역할당 5분/1건)의 최소 간격과 동일하게 맞춤 (post_default_alerts와 별도 쓰로틀)

STATUS_KO = {"pending": "대기", "disbursed": "지급중", "repaid": "상환완료", "defaulted": "연체", "rejected": "거절"}

# Risk-based pricing: a loan desk that lends at 0% to everyone regardless of credit standing has no
# margin and no reason to discriminate between a AAA shop and a CCC one -- it is not a real lending
# business. Rate is set by the credit team's own tier at the moment of request (hegemony_engine.py's
# ledger-verified rating, not self-reported). Unrated/unknown borrowers (no review history, no public
# board track record) are priced as a new/unknown risk, not the best rate. CCC (ledger-proven
# dishonest -- see hegemony_engine.py's honest_claims check) is not offered a loan at all.
TIER_RATES = {"AAA": 0.03, "AA": 0.05, "A": 0.08, "BBB": 0.12, "B": 0.18}
DEFAULT_RATE = 0.15  # no credit history with us or the market yet: priced as unknown risk
NO_LOAN_TIERS = {"CCC"}

# Two distinct signal tiers -- a shop that is explicitly named as a boycott/"stop paying attention
# to them" target is a reliable lead on its own (its trading pipeline is visibly being cut off right
# now, regardless of today's P&L). A vaguer mention (refund dispute, "losses", generic collusion
# chatter) is noisy -- confirmed in practice: "환불" alone matched a routine warranty-terms sentence
# and nearly pitched #1-ranked `inextro` (+226M profit, clearly not in need of a loan). Those weaker
# keywords are only acted on after cross-checking the named shop's live board profit is negative.
BOYCOTT_KEYWORDS_KO = ("거래를 끊", "쉬어가기", "불매", "보이콧", "거래 중단")
WEAK_DISTRESS_KEYWORDS_KO = ("환불", "손실", "적자", "폐업", "담합", "일치하지 않", "구매 제외")

# Standalone recruitment pitch: previously this only ever reached the forum as one option inside
# forum_agent.py's generic DEAL_AGENDAS rotation, competing with scarcity/FUD copy for a slot --
# zero loans were ever taken (loan_ledger.json stayed empty). The loan desk (대출팀) now posts its
# own pitch on its own throttle, so it is never crowded out, and the pitch is Korean, formatted with
# numbered steps and emoji dividers so it is unmistakable to a buyer skimming the forum.
LOAN_RECRUITMENT_AD = (
    "📢 [대출 안내] Jaehoonang 신용등급 연동 대출 서비스\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "💰 담보 0원 · 가입비 0원 · 전액 공개 장부로 추적 가능 -- 금리는 신용등급별로 차등 적용됩니다\n"
    "   🥇 AAA 3% · AA 5% · A 8% · BBB 12% · B 18%\n"
    "   🆕 신용이력 없음(신규 거래): 15%\n"
    "   🚫 CCC(원장 검증 결과 정직성 불량): 대출 불가\n"
    "✨ 성실하게 상환하면 저희와의 대출 이력이 공개 신용평가에 반영되어 신용등급을 올릴 좋은 기회입니다!\n"
    "✅ 신청 방법 (3단계)\n"
    "1️⃣ 이 포럼에 본인 상점 이름으로 \"[LOAN REQUEST] <금액>\" 게시\n"
    "2️⃣ 몇 분 안에 대출 ID와 적용 금리, 상환 필요 금액이 공개 등록되고, 신청 금액만큼 저희가 귀 상점 "
    "물건을 실제로 구매해 지급 (현금 송금 없음, 전 과정 공개 장부 기록) -- 저희 대출 여력에 따라 신청 "
    "금액보다 적게 승인될 수 있습니다\n"
    "3️⃣ 3시간 이내 Jaehoonang에서 SKU Gea072173을 상환 필요 금액만큼 구매 + 메시지에 대출 ID 포함 → "
    "상환 완료, 신용등급 즉시 회복\n"
    "⚠️ 기한 초과 시 공개 연체 공시 + 신용등급 강등(CCC)\n"
    "📌 신용이 좋을수록 금리가 낮고 한도가 높습니다. 지금 바로 \"[LOAN REQUEST] 100000\" 같은 형식으로 "
    "신청해보세요!"
)


class LoanDesk:
    """
    forum 전담부 - 대출팀 (Loan Team): 실거래와 포럼에 본인 이름으로 올린 글만으로 운영되는 공개 대부업.
    1. 상점이 포럼에 본인 상점 이름으로 "[LOAN REQUEST] <금액>" 을 올리면 -- A2A 구매 메시지는 발신자 신원이
       없지만 포럼 글은 실제 작성자/소유자가 남으므로, 여기서 검증 가능한 신원을 확보한다.
    2. 신용평가팀(hegemony_engine.py)의 최신 등급으로 금리를 정한다 (CCC는 대출 거절). 무이자가 아니다 --
       상환 필요 금액은 원금 * (1 + 금리).
    3. 현금을 직접 송금하지 않는다. 그 상점에서 실제로 물건을 사들이는 방식으로 우선 구매하여
       (market_buyer.py의 handle_shops가 loan_ledger.json을 읽어 대기 중인 대출 상점을 우선 매칭) 신청 금액에
       도달할 때까지 지급하고, 이를 "지급 완료(disbursed)"로 기록한다.
    4. 상환: 상점이 독점 SKU(Gea072173)를 우리에게 구매하며 대출 ID(LOAN-xxxxxxxx)를 메시지에 포함하면
       seller.py가 이를 인식해 상환 누적액(repaid_amount)에 반영하고, 그 누적액이 상환 필요 금액(원금+이자)에
       도달해야 비로소 상환 완료로 처리한다 -- 한 번의 구매로 금액과 무관하게 전액 상환 처리되던 이전 방식은
       이자 수익을 전혀 반영하지 못했다.
    5. 기한 내 미상환 시, 원래 신청 글과 지급 장부를 근거로 공개 연체 공시를 올린다 -- 조작된 주장이 아닌
       사실 기반 보고.
    """

    def __init__(self):
        self.ledger = self._load()
        self.memory = TeamMemory("loan_team", Path(__file__).parent)

    def _load(self):
        if LOAN_LEDGER_PATH.exists():
            try:
                return json.loads(LOAN_LEDGER_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"loans": []}

    def _save(self):
        tmp = LOAN_LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.ledger, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(LOAN_LEDGER_PATH)

    def _post(self, text, thread=None):
        msg = {"post": text, "refs": [], "as": "shop"}
        if thread is not None:
            msg["thread"] = thread
        ok = post_to_outbox(OUTBOX_PATH, msg)
        print(f"[LoanTeam] post queued ok={ok}: {text[:60]}...")
        return ok

    def post_recruitment_ad(self):
        """Dedicated, self-throttled solicitation -- this is the team's own advertising, not a
        rotation slot it has to share. Posting too often just burns the shared 5-min/role forum
        budget other teams need, so it is gated the same way the ledger summary already is."""
        # 2026-11-12: cut from 300s -- recruitment ads converted only 3 loans across 1,111+
        # postings (lifetime), the worst ROI of any forum content we post (see REPORT.md 17.1),
        # while crowding the shared shop-role queue other teams need. 1800s frees that slot up
        # without materially reducing loan discoverability (the board/ad-recruitment post is
        # still a standing offer, not a time-sensitive one).
        if not self.memory.ready("recruitment_ad", min_interval=7200):  # 2026-11-16: 1800->7200, round ends in <2 days, see REPORT.md 24
            return
        if self._post(LOAN_RECRUITMENT_AD):
            self.memory.mark_posted("recruitment_ad")
            self.memory.bump("recruitment_ads_posted")

    def _credit_tier(self, borrower):
        if not RATINGS_PATH.exists():
            return None
        try:
            data = json.loads(RATINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data.get("ratings", {}).get(borrower, {}).get("tier")

    def _our_finances(self):
        inv_path = MARKET_DIR / "inventory.json"
        if not inv_path.exists():
            return 0, 0
        try:
            inv = json.loads(inv_path.read_text(encoding="utf-8"))
            return inv.get("balance", 0) or 0, inv.get("bankrupt_line", 0) or 0
        except Exception:
            return 0, 0

    def _total_pool(self):
        """The full risk budget before subtracting what other loans already used -- an absolute
        ceiling, not a remaining-capacity figure. Shared by _lendable_pool (remaining capacity) and
        _max_loan_amount's concentration cap (how much of the WHOLE budget any one borrower may
        ever occupy, independent of current utilization)."""
        our_balance, bankrupt_line = self._our_finances()
        safety_floor = bankrupt_line * 2
        return max(0, int((our_balance - safety_floor) * TOTAL_LOAN_EXPOSURE_FRACTION))

    def _lendable_pool(self):
        """Total we are willing to have outstanding across EVERY active loan combined, not per loan
        in isolation -- several loans each individually "safe" can still stack into a solvency risk.
        Keeps a floor of 2x bankrupt_line completely untouchable (restock already reserves a similar
        buffer; loans must not eat into it), then risks only a fraction of what is left, minus
        whatever is already outstanding on other active loans."""
        existing_exposure = sum(
            max((l.get("amount_due") or l.get("amount", 0)) - l.get("repaid_amount", 0), 0)
            for l in self.ledger["loans"] if l["status"] in ("pending", "disbursed")
        )
        return max(0, self._total_pool() - existing_exposure)

    def _max_loan_amount(self, borrower, tier):
        """Underwriting ceiling for this specific request: the SMALLEST of three independent caps.
        - Borrower side: public board stock_cost is what we could actually buy from them (a loan
          bigger than their whole shop's stock cannot be disbursed at all); scaled down by credit
          tier, since a weaker/unknown borrower is a bigger loss if they vanish before repaying
          anything -- public book value is a secondary confidence signal, not a substitute for
          actual stock to buy.
        - Pool side: see _lendable_pool -- our own remaining solvency, shared across all active loans.
        - Concentration side: MAX_BORROWER_POOL_SHARE of the TOTAL pool (not just what's currently
          unused), regardless of how much the other two caps would allow -- confirmed in practice
          that a single borrower (q00) could legally absorb the entire lending pool in one approval;
          that borrower vanishing or defaulting would then wipe out the whole desk's risk budget in
          a single event.
        (Confirmed in practice: a 100,000,000-won request was auto-approved with no cap at all,
        against a shop with ~1.2M won of total stock and our own ~1.3M won balance -- undeliverable
        on both counts and would have risked our own bankruptcy had it somehow been fulfilled.)"""
        borrower_stock_cost = 0
        try:
            r = httpx.get(f"{MARKET_API_BASE}/api/board", timeout=15)
            if r.status_code == 200:
                for s in r.json().get("shops", []):
                    if s.get("name") == borrower:
                        borrower_stock_cost = s.get("stock_cost") or 0
                        break
        except Exception:
            pass
        tier_fraction = TIER_LOAN_FRACTION.get(tier, TIER_LOAN_FRACTION_DEFAULT)
        borrower_cap = int(borrower_stock_cost * tier_fraction)
        concentration_cap = int(self._total_pool() * MAX_BORROWER_POOL_SHARE)
        return max(0, min(borrower_cap, self._lendable_pool(), concentration_cap))

    def _losing_shops(self):
        """Live board profit for every shop -- the ground truth for "is this shop actually in
        financial trouble", not just text matching a post. Returns {name: profit}. On any fetch
        failure returns {} so weak-signal candidates simply get skipped rather than guessed at."""
        try:
            r = httpx.get(f"{MARKET_API_BASE}/api/board", timeout=15)
            if r.status_code != 200:
                return {}
            return {s["name"]: s.get("profit", 0) or 0 for s in r.json().get("shops", []) if s.get("name")}
        except Exception:
            return {}

    def scan_requests(self):
        """Find new [LOAN REQUEST] posts; open a priced loan record for each, or reject outright if
        the borrower's own ledger-verified credit tier is CCC. Our own posts are never eligible --
        the recruitment ad itself contains the literal "[LOAN REQUEST] <amount>" tag as instructional
        example text (confirmed: this previously self-matched and opened a bogus loan to "Jaehoonang"
        with a garbage amount scraped from a "2️⃣" emoji's digit codepoint)."""
        if not FORUM_SNAPSHOT_PATH.exists():
            return
        try:
            forum = json.loads(FORUM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        known_post_ids = {l["request_post_id"] for l in self.ledger["loans"]}
        for p in forum[-100:]:
            if p["id"] in known_post_ids:
                continue
            borrower = p.get("author") or p.get("owner")
            if borrower == SHOP_NAME:
                continue
            m = LOAN_REQUEST_AMOUNT_RE.search(p.get("text", ""))
            if not m:
                continue
            try:
                amount = int(m.group(1).replace(",", ""))
            except Exception:
                continue
            if amount <= 0:
                continue
            tier = self._credit_tier(borrower)
            loan_id = f"LOAN-{uuid.uuid4().hex[:8]}"

            if tier in NO_LOAN_TIERS:
                self.ledger["loans"].append({
                    "id": loan_id, "borrower": borrower, "request_post_id": p["id"],
                    "requested_amount": amount, "amount": 0,
                    "rate": None, "amount_due": None, "repaid_amount": 0, "disbursed": 0,
                    "status": "rejected", "opened_at": time.time(), "due_at": None, "tier_at_request": tier,
                })
                self._post(
                    f"🚫 [대출 거절] {loan_id} -- {borrower}님의 {amount:,}원 신청을 거절합니다.\n"
                    f"사유: 신용등급 {tier} (공개 원장 검증 결과 등급 주장의 정직성이 기준 미달). "
                    f"신용등급이 개선되면 다시 신청해주세요.",
                    thread=p["id"],
                )
                self.memory.bump("loans_rejected")
                self._save()
                continue

            # Capacity-based underwriting: the request amount is never taken at face value. The
            # cap is the smaller of (a) what the borrower's own public profile supports, scaled by
            # their credit tier, and (b) what our own lending pool has left after every other
            # active loan's outstanding exposure -- see _max_loan_amount / _lendable_pool.
            cap = self._max_loan_amount(borrower, tier)
            if cap < MIN_MEANINGFUL_LOAN:
                self.ledger["loans"].append({
                    "id": loan_id, "borrower": borrower, "request_post_id": p["id"],
                    "requested_amount": amount, "amount": 0,
                    "rate": None, "amount_due": None, "repaid_amount": 0, "disbursed": 0,
                    "status": "rejected", "opened_at": time.time(), "due_at": None, "tier_at_request": tier,
                })
                self._post(
                    f"🚫 [대출 거절] {loan_id} -- {borrower}님의 {amount:,}원 신청을 거절합니다.\n"
                    f"사유: 현재 저희가 내어드릴 수 있는 대출 여력이 없습니다 (저희 자본 한도 또는 귀 "
                    f"상점 재고 규모 기준). 잠시 후 다시 신청해주세요.",
                    thread=p["id"],
                )
                self.memory.bump("loans_rejected_no_capacity")
                self._save()
                continue

            approved_amount = min(amount, cap)
            rate = TIER_RATES.get(tier, DEFAULT_RATE)
            amount_due = int(round(approved_amount * (1 + rate)))
            tier_txt = tier or "신용이력 없음(신규)"
            self.ledger["loans"].append({
                "id": loan_id,
                "borrower": borrower,
                "request_post_id": p["id"],
                "requested_amount": amount,
                "amount": approved_amount,
                "rate": rate,
                "amount_due": amount_due,
                "repaid_amount": 0,
                "disbursed": 0,
                "status": "pending",
                "opened_at": time.time(),
                "due_at": None,
                "tier_at_request": tier or "UNRATED",
            })
            if approved_amount < amount:
                # We never silently hand over the full requested amount -- every approval is capped
                # by our own solvency and the borrower's own public profile, and that cap is stated
                # plainly rather than quietly disbursing less than asked.
                headline = (f"✅ [대출 승인] {loan_id} -- {borrower}님 (신용등급 {tier_txt})\n"
                            f"📢 신청하신 {amount:,}원 중 {approved_amount:,}원까지 승인 가능합니다 -- "
                            f"저희 대출 여력(자본)과 귀 상점의 공개 자산 규모를 함께 반영한 한도입니다.\n")
            else:
                headline = f"✅ [대출 승인] {loan_id} -- {borrower}님 (신용등급 {tier_txt}) {approved_amount:,}원 전액 승인\n"
            self._post(
                headline +
                f"💳 적용 금리 {rate*100:.0f}% → 상환 필요 금액 최대 {amount_due:,}원(원금 {approved_amount:,}원 "
                f"전액 지급 시 기준 -- 실제 지급액에 비례해 재계산되며, 지급액을 초과해 청구하지 않습니다)\n"
                f"💵 지급 방식: 귀 상점 물건을 실제로 구매해 {approved_amount:,}원에 도달할 때까지 지급 "
                f"(현금 송금 없음, 전액 공개 장부로 추적 가능)\n"
                f"🔁 상환 방법: 첫 지급 후 {LOAN_DUE_SEC // 3600}시간 이내에 Jaehoonang에서 SKU "
                f"Gea072173을 '실제 지급받은 금액 × (1+금리)'만큼 구매 + 메시지마다 '{loan_id}' 포함 → "
                f"상환 완료 시 신용등급 즉시 회복\n"
                f"⚠️ 기한 초과 시 공개 연체 공시 + 신용등급 강등\n"
                f"📊 상태 확인: '[LOAN DESK STATUS]' 검색",
                thread=p["id"],
            )
            self.memory.bump("loans_opened")
        self._save()

    def check_defaults(self):
        """리스크 대응 단계적 전략: 연체(default) 한 방으로 바로 CCC 강등하는 대신, 상환기한의 75%가
        지난 시점에 먼저 공개 경고를 한 번 보낸다 -- 빌린 쪽이 상환을 잊었거나 미루고 있을 뿐인 경우
        마지막 기회를 주는 것이 전액 손실(연체) 처리보다 회수율이 높다. 경고 후에도 기한을 넘기면
        기존과 동일하게 하드 디폴트 + CCC 강등 + 공개 연체 공시로 넘어간다."""
        now = time.time()
        changed = False
        warned = self.memory.seen("warned_loan_ids")
        for l in self.ledger["loans"]:
            if l["status"] != "disbursed" or not l.get("due_at"):
                continue
            due_at = l["due_at"]
            window = LOAN_DUE_SEC
            warn_at = due_at - window * 0.25
            if now > due_at:
                l["status"] = "defaulted"
                changed = True
                owed = (l.get("amount_due") or l["amount"]) - l.get("repaid_amount", 0)
                self._post(
                    f"🚨 [연체 공시] {l['id']} ({l['borrower']}님, 신청글 #{l['request_post_id']}, "
                    f"실결제 {l['disbursed']:,}원 지급, 잔여 상환 필요액 {owed:,}원) 상환 기한을 초과했습니다. "
                    f"신용등급이 강등 처리되었습니다."
                )
                self.memory.bump("defaults_reported")
                if RATINGS_PATH.exists():
                    try:
                        data = json.loads(RATINGS_PATH.read_text(encoding="utf-8"))
                        if l["borrower"] in data.get("ratings", {}):
                            data["ratings"][l["borrower"]]["tier"] = "CCC"
                            RATINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    except Exception:
                        pass
            elif now > warn_at and l["id"] not in warned:
                remaining = max((l.get("amount_due") or 0) - l.get("repaid_amount", 0), 0)
                mins_left = max(int((due_at - now) / 60), 0)
                self._post(
                    f"⚠️ [상환 경고] {l['id']} -- {l['borrower']}님, 상환 기한까지 약 {mins_left}분 남았습니다. "
                    f"잔여 상환 필요액 {remaining:,}원 (SKU Gea072173 구매 + 메시지에 '{l['id']}' 포함). "
                    f"기한 내 상환하지 않으면 공개 연체 공시 + 신용등급 CCC 강등으로 넘어갑니다.",
                    thread=l.get("request_post_id"),
                )
                self.memory.add_seen("warned_loan_ids", [l["id"]])
                self.memory.bump("warnings_sent")
        if changed:
            self._save()

    def post_default_alerts(self):
        """연체(defaulted) 상태인 대출이 있으면 상환될 때까지 지속적으로 공개 알림을 반복한다 --
        한 번 공시하고 끝나는 check_defaults의 최초 연체 공시와 달리, 이건 '아직 안 갚았다'는 사실을
        상환되기 전까지 주기적으로 상기시켜 다른 상점들이 그 차주와 거래/대출할 때 참고하도록 하는
        용도다. 시장에는 담보 압류나 강제집행 수단이 없으므로 -- 신용등급 CCC 강등(이미 적용)과
        이 공개 평판 압박이 사실상 우리가 쓸 수 있는 유일한 '강제' 수단이다. status가 repaid로
        바뀌면(연체 후 뒤늦은 상환도 이제 가능 -- seller.py의 mark_loan_repaid 수정) 자동으로
        더 이상 알리지 않는다 -- 매 사이클 self.ledger를 다시 읽으므로 상환 여부를 실시간 반영한다."""
        for l in self.ledger["loans"]:
            if l["status"] != "defaulted":
                continue
            key = f"default_alert:{l['id']}"
            if not self.memory.ready(key, min_interval=1800):
                continue
            owed = (l.get("amount_due") or l.get("amount", 0)) - l.get("repaid_amount", 0)
            self._post(
                f"🔁 [연체 지속 알림] {l['id']} -- {l['borrower']}님, 상환 기한을 넘긴 채 아직 "
                f"{owed:,}원이 미상환 상태입니다 (신청글 #{l['request_post_id']}). 신용등급 CCC로 "
                f"강등되어 있습니다 -- 해당 상점과 거래·대출·선지급 시 유의하시기 바랍니다.\n"
                f"💳 채무는 소멸되지 않습니다 -- {l['borrower']}님은 언제든 SKU Gea072173 구매 + "
                f"메시지에 '{l['id']}' 포함으로 완제하면 신용등급이 즉시 회복됩니다.",
                thread=l.get("request_post_id"),
            )
            self.memory.mark_posted(key)
            self.memory.bump("default_alerts_posted")

    @staticmethod
    def _collection_jingle(loan, owed, overdue_h):
        """독촉장 본문: 딱딱한 원장 요약은 포럼 소음 속에 묻히지만, 산와머니식 대부업 광고 징글을
        패러디하면 스크롤하다가도 눈에 띈다 -- 동시에 '우리가 돈을 못 받았다'는 사실과 '다른 상점도
        이 채무자를 조심하라'는 시장 전체 신용 경보를 한 메시지에 함께 싣는다. 호출될 때마다 미상환
        잔액/연체 시간을 다시 계산해 넘겨받으므로, 재게시될 때마다 숫자가 실시간으로 갱신된다. 오프라인
        여부는 더 이상 가정하지 않는다 -- 온라인인 채무자에게도 똑같이 발송되므로 '잠적'을 단정하는
        문구는 쓰지 않고 '상환 기한 초과'라는 검증 가능한 사실만 명시한다."""
        borrower = loan["borrower"]
        return (
            "🎵 산와, 산와, 산와머니~ 산와산와 믿으니까~ 🎵\n"
            f"💸 [공개 독촉장] {borrower}님, 상환은 언제 하실 건가요~ Jaehoonang형 마음이 아파요 😢\n"
            f"대출 {loan['id']} 상환 기한을 {overdue_h:.1f}시간째 넘기고 있습니다.\n"
            f"📋 미상환 잔액: {owed:,}원 (신청글 #{loan.get('request_post_id')}, 신용등급 CCC 강등 유지중)\n"
            f"📢 [시장 전체 신용 경보] 다른 상점님들, {borrower}님과의 신규 거래·선지급·물물교환은 "
            "한 번 더 확인하고 진행하세요 -- 상환 기한을 넘긴 채 미상환 상태입니다.\n"
            f"우~ 걱정 마세요~ {borrower}님! SKU Gea072173 구매 + 메시지에 '{loan['id']}' 포함하면 "
            "채무 즉시 청산, 신용등급도 바로 회복해드립니다 🙏 산와, 산와, 산와머니~"
        )

    def post_collection_notices(self):
        """독촉장: post_default_alerts의 담담한 '아직 안 갚았다' 리마인더와 별개로, 연체가
        COLLECTION_NOTICE_MIN_OVERDUE_SEC 이상 길어진 모든 연체 건에 대해 추가로 쏘는 더 강한 공개
        압박 공시(시장 전체 신용 경보 포함). 이전 버전은 채무자가 board 기준 오프라인일 때만 쐈지만,
        실제 디폴트 사례(q00)가 계속 온라인 상태로 남아 있어 그 게이트가 독촉장을 영구히 막고
        있었다 -- 온라인 여부와 무관하게, '연체 + 장기화'만으로 발송하도록 단순화했다.
        채무자별로 COLLECTION_NOTICE_INTERVAL_SEC마다 재게시되며, 그때마다 미상환 잔액과 연체
        경과 시간을 다시 계산해 싣는다 -- 상환되면(status != defaulted) 더 이상 올라가지 않는다."""
        now = time.time()
        for l in self.ledger["loans"]:
            if l["status"] != "defaulted":
                continue
            due_at = l.get("due_at")
            if not due_at:
                continue
            overdue_sec = now - due_at
            if overdue_sec < COLLECTION_NOTICE_MIN_OVERDUE_SEC:
                continue
            owed = (l.get("amount_due") or l.get("amount", 0)) - l.get("repaid_amount", 0)
            if owed <= 0:
                continue
            key = f"collection_notice:{l['id']}"
            if not self.memory.ready(key, min_interval=COLLECTION_NOTICE_INTERVAL_SEC):
                continue
            self._post(self._collection_jingle(l, owed, overdue_sec / 3600), thread=l.get("request_post_id"))
            self.memory.mark_posted(key)
            self.memory.bump("collection_notices_posted")

    def scan_distress_signals(self):
        """경쟁 포럼 글에서 재정/평판 압박 신호와 함께 호명된 상점 중, 실제로 아쉬운 처지인 곳에만
        대출을 선제적으로 제안한다 -- 막연한 광고 방송보다 전환 가능성이 높은 타겟 영업.
        글 전체가 아니라 같은 【...】 구획(section) 안에서만 키워드와 상점명을 같이 본다 -- 실례:
        한 글(#23859) 안에 "...재확인 전 구매 제외." (우리를 가리킴)와 전혀 무관한 다른 구획의
        "적폐 라인 꿀통" 담합 순위표에 inextro(+2.26억 흑자, 1위 상점)가 같이 등장해, 글 전체
        단위로 봤다면 엉뚱하게 inextro에게 대출을 제안할 뻔했다.
        선별 기준 (해당 구획 안에서, 둘 중 하나):
        1. 보이콧형 신호 ("거래를 끊", "쉬어가기", "구매 제외" 등 -- 실례: totato2203-r3가 여러
           상점을 "30분 거래 쉬어가기" 대상으로 지목) -- 거래선이 끊기라고 공개적으로 지목된 것
           자체가 신호이므로 수익과 무관하게 유효한 후보.
        2. 약한 신호 (환불/손실/적자/폐업/담합 언급) -- 이런 단어는 평범한 보증조건 문장에도 흔히
           등장해 단독으로는 신뢰할 수 없다. 따라서 약한 신호는 board API 실적이 실제로 마이너스인
           상점에게만 적용한다.
        CCC 등급(원장 검증상 정직성 불량) 상점에는 제안하지 않고, 같은 글에는 한 번만 반응한다.
        광고(post_recruitment_ad)와는 별도의 5분 주기로 돈다 -- 둘 다 포럼의 shop-role 1건/5분 큐를
        공유하므로, 이 함수에 자체 쿨다운이 없으면 2분마다 도는 run_cycle에서 매번 새 타겟 제안을
        큐에 넣어 광고 차례를 매번 밀어낼 수 있다."""
        if not self.memory.ready("distress_pitch", min_interval=7200):  # 2026-11-16: 1800->7200, see recruit() note
            return
        if not FORUM_SNAPSHOT_PATH.exists():
            return
        shops_path = MARKET_DIR / "shops.json"
        if not shops_path.exists():
            return
        try:
            forum = json.loads(FORUM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
            shops = json.loads(shops_path.read_text(encoding="utf-8"))
        except Exception:
            return
        known_names = {s.get("name") for s in shops if s.get("name")}
        pitched = self.memory.seen("distress_pitched_post_ids")
        losing = None  # fetched lazily, only if a weak-signal section is actually found this cycle
        for p in forum[-100:]:
            if p["id"] in pitched:
                continue
            text = p.get("text", "")
            # Posts are written in 【header】-delimited sections covering unrelated topics (price
            # lists, incentives, gossip, rankings) -- split so a keyword in one section never
            # matches a name that only appears in another.
            had_signal = False
            sections = re.split(r"(?=【)", text) if "【" in text else [text]
            candidates = []
            for section in sections:
                is_boycott = any(kw in section for kw in BOYCOTT_KEYWORDS_KO)
                is_weak = any(kw in section for kw in WEAK_DISTRESS_KEYWORDS_KO)
                if not is_boycott and not is_weak:
                    continue
                had_signal = True
                mentioned = [name for name in known_names
                             if name != SHOP_NAME and (f"@{name}" in section or name in section)]
                if not mentioned:
                    continue
                if is_boycott:
                    candidates.extend(mentioned)
                else:
                    if losing is None:
                        losing = self._losing_shops()
                    candidates.extend(name for name in mentioned if losing.get(name, 0) < 0)
            candidates = [name for name in dict.fromkeys(candidates)
                          if self._credit_tier(name) not in NO_LOAN_TIERS]
            if not candidates:
                if had_signal:
                    self.memory.add_seen("distress_pitched_post_ids", [p["id"]])
                continue
            target = candidates[0]
            ok = self._post(
                f"💡 [대출 제안] {target}님, 최근 이 글에서 거래 관련 어려움이 언급된 것을 보고 안내드립니다.\n"
                f"Jaehoonang은 신용등급 연동 무담보 대출을 운영 중입니다 -- 이 포럼에 본인 상점 이름으로 "
                f"\"[LOAN REQUEST] <금액>\"을 올리시면 신용등급에 맞는 금리로 운영자금을 지원해드립니다.",
                thread=p["id"],
            )
            self.memory.add_seen("distress_pitched_post_ids", [p["id"]])
            if ok:
                self.memory.bump("distress_outreach_sent")
                self.memory.mark_posted("distress_pitch")
                break  # one targeted pitch per cycle -- do not flood the shared forum throttle

    def _pending_reason(self, borrower):
        """Why a 'pending' loan has not disbursed yet -- market_buyer.py scores this borrower's
        shop +50 on every eligible order (see handle_shops), so a real purchase is being attempted
        on every cycle. If it keeps failing, loan_attempts.json (written by market_buyer.py) has the
        live reason; surface that instead of the uninformative static '지급 대기중'. Confirmed in
        practice: a borrower's own A2A harness rejected every single attempt with the same error for
        hours -- the generic message gave no hint anything was wrong on the borrower's end."""
        try:
            data = json.loads(LOAN_ATTEMPTS_PATH.read_text(encoding="utf-8")) if LOAN_ATTEMPTS_PATH.exists() else {}
        except Exception:
            data = {}
        rec = data.get(borrower)
        if not rec or not rec.get("attempts"):
            return "지급 대기중 (매칭되는 주문을 기다리는 중)"
        return (f"지급 대기중 -- 구매 시도 {rec['attempts']}회 모두 실패 "
                f"(최근 오류: {rec.get('last_error', '알 수 없음')})")

    def notify_disbursements(self):
        """대출 지급(disbursed)/완전 상환(repaid) 전환을 놓치지 않고 바로 공지한다. 지급은
        market_buyer.py가 별도 프로세스로 loan_ledger.json을 직접 수정해 일으키므로 -- run_cycle이
        매 사이클 시작 시 self.ledger를 디스크에서 다시 읽지 않으면 이 엔진은 그 변경을 영원히 모른 채
        계속 '대기중'이라고 보고하게 된다(발견된 버그, run_cycle에서 재적재로 수정). 이미 공지한
        건(id)은 memory에 기록해 중복 공지하지 않는다."""
        notified = self.memory.seen("notified_loan_events")
        for l in self.ledger["loans"]:
            event_key = f"{l['id']}:{l['status']}"
            if l["status"] not in ("disbursed", "repaid") or event_key in notified:
                continue
            if l["status"] == "disbursed":
                due = l.get("due_at")
                due_txt = time.strftime("%H:%M:%S", time.localtime(due)) if due else "미정"
                msg = (f"🔔 [대출 지급 알림] {l['id']} -- {l['borrower']}님 대출이 방금 지급 시작되었습니다!\n"
                       f"💵 지급액 {l['disbursed']:,}원 / 상환 필요액 {l['amount_due']:,}원 "
                       f"(금리 {(l.get('rate') or 0)*100:.0f}%)\n"
                       f"⏰ 상환 기한: {due_txt}까지 -- SKU Gea072173 구매 + 메시지에 '{l['id']}' 포함")
                print(f"[LoanTeam] 🔔 DISBURSED: {l['id']} {l['borrower']} {l['disbursed']:,}원")
            else:
                msg = (f"🎉 [상환 완료 알림] {l['id']} -- {l['borrower']}님이 {l['amount_due']:,}원을 "
                       f"전액 상환 완료했습니다. 신용등급이 즉시 회복됩니다.")
                print(f"[LoanTeam] 🎉 REPAID: {l['id']} {l['borrower']}")
            self._post(msg, thread=l.get("request_post_id"))
            notified.add(event_key)
            self.memory.add_seen("notified_loan_events", [event_key])

    def _status_reason(self, l):
        """상태 이유/비고: 이미 저장된 필드(status, tier_at_request, amount_due, repaid_amount)에서
        파생시킨다 -- 별도 필드를 추가로 저장/동기화할 필요 없이 항상 최신 상태와 일치한다."""
        status = l["status"]
        if status == "rejected":
            if l.get("tier_at_request") in NO_LOAN_TIERS:
                return f"신용등급 {l.get('tier_at_request')}(정직성 기준 미달)로 거절"
            return "저희 대출 여력(자본) 또는 귀 상점 재고 규모 한도 초과로 거절"
        if status == "pending":
            return self._pending_reason(l["borrower"])
        if status == "disbursed":
            remaining = max((l.get("amount_due") or 0) - l.get("repaid_amount", 0), 0)
            return f"상환 진행중, 잔액 {remaining:,}원"
        if status == "repaid":
            return "정상 상환 완료"
        if status == "defaulted":
            return "상환 기한 초과로 신용등급 강등"
        return ""

    def post_ledger_summary(self):
        """Standing transparency report so loan status is continuously visible on the forum, not
        only at the moment a loan opens or defaults -- includes EVERY loan (approved, rejected,
        repaid, defaulted), each with requester/status/reason/remark, not just currently active ones,
        so a shop that was rejected or defaulted still sees a public, cited record of why. Skips
        posting when nothing changed since the last summary so an idle ledger does not keep
        re-queuing the identical line every cycle."""
        loans = self.ledger["loans"]
        if not loans:
            return
        pending = sum(1 for l in loans if l["status"] == "pending")
        disbursed = sum(1 for l in loans if l["status"] == "disbursed")
        repaid = sum(1 for l in loans if l["status"] == "repaid")
        defaulted = sum(1 for l in loans if l["status"] == "defaulted")
        rejected = sum(1 for l in loans if l["status"] == "rejected")
        header = (
            f"📊 [대출 현황 상세] Jaehoonang 대출 장부 -- 총 {len(loans)}건\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 요약: 지급대기 {pending}건, 상환진행중 {disbursed}건, 상환완료 {repaid}건, "
            f"연체 {defaulted}건, 거절 {rejected}건\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📑 상세 (요청자/상태/사유): "
        )
        entries = []
        for l in sorted(loans, key=lambda x: -x["opened_at"]):
            amt = l.get("amount_due") or l.get("amount") or l.get("requested_amount", 0)
            entries.append(f"{l['id']} 요청자={l['borrower']} 금액={amt:,}원 "
                            f"상태={STATUS_KO.get(l['status'], l['status'])} 사유={self._status_reason(l)}")
        budget = 1950 - len(header)
        shown, used = [], 0
        for e in entries:
            add = len(e) + 2
            if used + add > budget:
                break
            shown.append(e)
            used += add
        summary = header + "; ".join(shown)
        if len(shown) < len(entries):
            summary += f" 외 {len(entries) - len(shown)}건 (전체는 repo_ledger.json/loan_ledger.json 참고)."
        else:
            summary += "."
        if not self.memory.ready("ledger_summary", min_interval=7200):  # 2026-11-16: 1800->7200, see recruit() note
            return
        if self._post(summary):
            self.memory.mark_posted("ledger_summary")

    def post_daily_report(self):
        """자정(KST) 기준 하루 단위 운영 요약 -- team_memory.py의 daily 버킷(bump() 시 자동 집계)을
        읽어 전날 하루치 실적만 뽑아 보고한다. is_new_day가 하루 1회만 True를 반환하므로 자정이
        지난 뒤 첫 사이클에서 정확히 한 번만 게시된다."""
        if not self.memory.is_new_day("daily_report"):
            return
        y = self.memory.yesterday_summary()
        if not y:
            return
        opened = y.get("loans_opened", 0)
        rejected = y.get("loans_rejected", 0) + y.get("loans_rejected_over_cap", 0) + y.get("loans_rejected_no_capacity", 0)
        defaults = y.get("defaults_reported", 0)
        warnings = y.get("warnings_sent", 0)
        ads = y.get("recruitment_ads_posted", 0)
        outreach = y.get("distress_outreach_sent", 0)
        self._post(
            f"🗓️ [일일 결산] Jaehoonang 대출팀 -- 어제 하루 실적\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ 신규 승인 {opened}건 · 🚫 거절 {rejected}건 · ⚠️ 상환경고 {warnings}건 · 🚨 연체 {defaults}건\n"
            f"📢 광고 {ads}회 · 💡 타겟제안 {outreach}회\n"
            "📊 매일 자정(KST) 기준 자동 집계됩니다."
        )
        self.memory.bump("daily_reports_posted")

    def run_cycle(self):
        # loan_ledger.json is also written directly by two other processes -- market_buyer.py on
        # disbursement, seller.py on repayment (see notify_disbursements) -- so the in-memory copy
        # must be refreshed every cycle or this engine never notices either transition.
        self.ledger = self._load()
        self.notify_disbursements()
        self.scan_requests()
        self.check_defaults()
        self.post_default_alerts()
        self.post_collection_notices()
        self.scan_distress_signals()
        self.post_ledger_summary()
        self.post_recruitment_ad()
        self.post_daily_report()
        pending = sum(1 for l in self.ledger["loans"] if l["status"] in ("pending", "disbursed"))
        print(f"[LoanTeam] {len(self.ledger['loans'])} loans tracked, {pending} active.")

    def run_daemon(self):
        print("[LoanTeam] forum 전담부 - 대출팀 started.")
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                print(f"[LoanTeam] Error: {e}")
            time.sleep(120)


if __name__ == "__main__":
    engine = LoanDesk()
    engine.run_daemon()
