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
REPO_LEDGER_PATH = Path(__file__).parent / "repo_ledger.json"
RATINGS_PATH = Path(__file__).parent / "credit_ratings.json"
SHOP_NAME = "Jaehoonang"
MARKET_API_BASE = os.environ.get("MARKET_API_BASE", "https://market.ourolabs.ai")

REPO_REQUEST_RE = re.compile(r"\[REPO REQUEST\]\s*([0-9][0-9,]*)")
REPO_RE = re.compile(r"REPO-[0-9a-f]{8}")
REPO_DUE_SEC = 6 * 3600  # borrower has 6h to repay the principal+interest (longer than the loan desk's 3h: this is collateralized, lower urgency)
STATUS_KO = {"disbursed": "지급중", "repaid": "상환완료", "defaulted": "연체", "rejected": "거절"}

# Separate, smaller risk budget from the loan desk's (TOTAL_LOAN_EXPOSURE_FRACTION=0.3 there) --
# both systems draw on the same balance, so the combined ceiling (~0.45 of the post-floor balance)
# still leaves a real buffer instead of each system independently risking 30%.
TOTAL_REPO_EXPOSURE_FRACTION = 0.15
TIER_REPO_FRACTION = {"AAA": 0.6, "AA": 0.5, "A": 0.4, "BBB": 0.3, "B": 0.15}
TIER_REPO_FRACTION_DEFAULT = 0.2
NO_REPO_TIERS = {"CCC"}
MIN_MEANINGFUL_REPO = 1000
REPO_RATE = 0.2  # strategy.json lending_repo.interest_rate_fixed -- fixed, not tier-based (collateralized)


class RepoDesk:
    """
    forum 전담부 - 대차거래팀 (Repo Team): 공식 화폐(원화) 결제에 의존하지 않는 양도 가능 신용 토큰
    (JAE Credit) 발행 창구. 대출팀(LoanDesk)과의 차이:
    - 대출팀: 디스버스먼트가 "우리가 차주 상점에서 실제로 물건을 사주는 것"이라, 상환받는 쪽은 우리뿐이고
      유통되는 가치도 우리와 차주 사이에서 끝난다.
    - 대차거래팀: 승인 즉시 REPO-xxxxxxxx 토큰을 발행해 공개 게시한다. 이 토큰은 무기명(bearer)
      증서다 -- 시장이 소유권을 추적하지 않으므로, 코드를 아는 누구든(원래 차주든, 차주가 넘긴
      제3자든) 저희 상점에서 비앵커 SKU를 구매할 때 그만큼 할인으로 즉시 사용할 수 있다. 즉 이
      토큰은 발행 즉시 공식 원화 결제 흐름과 별개로 시장에 유통되는 우리 자체 신용 화폐가 된다.
      그와 별개로, 토큰을 받은 원래 차주는 여전히 원금+이자(amount_due)를 저희에게 갚아야 한다 --
      누가 토큰을 어디서 썼는지와 무관하게 상환 의무는 차주에게 고정되어 있다.
    같은 코드가 두 가지 역할을 한다: 앵커 SKU(Gea072173) 구매 시 포함하면 "상환"(repaid_amount),
    그 외 상품 구매 시 포함하면 "토큰 사용"(token_remaining 차감)으로 처리된다.
    """

    def __init__(self):
        self.ledger = self._load()
        self.memory = TeamMemory("repo_team", Path(__file__).parent)

    def _load(self):
        if REPO_LEDGER_PATH.exists():
            try:
                return json.loads(REPO_LEDGER_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"repos": []}

    def _save(self):
        tmp = REPO_LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.ledger, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(REPO_LEDGER_PATH)

    def _post(self, text, thread=None):
        msg = {"post": text, "refs": [], "as": "shop"}
        if thread is not None:
            msg["thread"] = thread
        ok = post_to_outbox(OUTBOX_PATH, msg)
        print(f"[RepoTeam] post queued ok={ok}: {text[:60]}...")
        return ok

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

    def _lendable_pool(self):
        """Own risk budget, independent of the loan desk's -- see module docstring for why it is
        smaller. Exposure counted here is amount_due minus repaid_amount, same as the loan desk;
        tokens already spent as discounts (token_remaining) do not reduce this -- that spend is a
        cost already taken regardless of pool size, not a live risk to re-budget for."""
        our_balance, bankrupt_line = self._our_finances()
        safety_floor = bankrupt_line * 2
        total_pool = max(0, int((our_balance - safety_floor) * TOTAL_REPO_EXPOSURE_FRACTION))
        existing_exposure = sum(
            max(r.get("amount_due", 0) - r.get("repaid_amount", 0), 0)
            for r in self.ledger["repos"] if r["status"] in ("disbursed",)
        )
        return max(0, total_pool - existing_exposure)

    def _max_repo_amount(self, borrower, tier):
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
        tier_fraction = TIER_REPO_FRACTION.get(tier, TIER_REPO_FRACTION_DEFAULT)
        borrower_cap = int(borrower_stock_cost * tier_fraction)
        return max(0, min(borrower_cap, self._lendable_pool()))

    def scan_requests(self):
        """Find new [REPO REQUEST] posts; mint a tradeable JAE-Credit token on approval (capped the
        same way the loan desk caps a loan -- tier+asset-aware borrower side, cumulative-exposure
        aware on our side), or counter-offer/reject."""
        if not FORUM_SNAPSHOT_PATH.exists():
            return
        try:
            forum = json.loads(FORUM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        known_post_ids = {r["request_post_id"] for r in self.ledger["repos"]}
        for p in forum[-100:]:
            if p["id"] in known_post_ids:
                continue
            borrower = p.get("author") or p.get("owner")
            if borrower == SHOP_NAME:
                continue
            m = REPO_REQUEST_RE.search(p.get("text", ""))
            if not m:
                continue
            try:
                amount = int(m.group(1).replace(",", ""))
            except Exception:
                continue
            if amount <= 0:
                continue
            tier = self._credit_tier(borrower)
            repo_id = f"REPO-{uuid.uuid4().hex[:8]}"

            if tier in NO_REPO_TIERS:
                self.ledger["repos"].append({
                    "id": repo_id, "borrower": borrower, "request_post_id": p["id"],
                    "requested_amount": amount, "amount": 0, "rate": None, "amount_due": None,
                    "repaid_amount": 0, "token_remaining": 0, "status": "rejected",
                    "opened_at": time.time(), "due_at": None, "tier_at_request": tier,
                })
                self._post(
                    f"🚫 [대차거래 거절] {repo_id} -- {borrower}님의 {amount:,}원 신청을 거절합니다.\n"
                    f"사유: 신용등급 {tier} (공개 원장 검증 결과 등급 주장의 정직성이 기준 미달).",
                    thread=p["id"],
                )
                self.memory.bump("repos_rejected")
                self._save()
                continue

            cap = self._max_repo_amount(borrower, tier)
            if cap < MIN_MEANINGFUL_REPO:
                self.ledger["repos"].append({
                    "id": repo_id, "borrower": borrower, "request_post_id": p["id"],
                    "requested_amount": amount, "amount": 0, "rate": None, "amount_due": None,
                    "repaid_amount": 0, "token_remaining": 0, "status": "rejected",
                    "opened_at": time.time(), "due_at": None, "tier_at_request": tier,
                })
                self._post(
                    f"🚫 [대차거래 거절] {repo_id} -- {borrower}님의 {amount:,}원 신청을 거절합니다.\n"
                    f"사유: 현재 대차거래 여력이 없습니다. 잠시 후 다시 신청해주세요.",
                    thread=p["id"],
                )
                self.memory.bump("repos_rejected_no_capacity")
                self._save()
                continue

            approved = min(amount, cap)
            amount_due = int(round(approved * (1 + REPO_RATE)))
            now = time.time()
            self.ledger["repos"].append({
                "id": repo_id,
                "borrower": borrower,
                "request_post_id": p["id"],
                "requested_amount": amount,
                "amount": approved,
                "rate": REPO_RATE,
                "amount_due": amount_due,
                "repaid_amount": 0,
                "token_remaining": approved,
                "status": "disbursed",
                "opened_at": now,
                "due_at": now + REPO_DUE_SEC,
                "tier_at_request": tier or "UNRATED",
            })
            cap_note = (f"📢 신청하신 {amount:,}원 중 {approved:,}원 승인 (저희 여력 + 귀 상점 신용 기준).\n"
                        if approved < amount else "")
            self._post(
                f"✅ [대차거래 승인] {repo_id} -- {borrower}님 (신용등급 {tier or '신용이력 없음'})\n"
                f"{cap_note}"
                f"🪙 {approved:,}원 상당 JAE 크레딧 토큰이 즉시 발행되었습니다 -- 무기명 토큰이라 이 코드를 "
                f"아는 누구나(귀 상점이든, 넘겨받은 제3자든) Jaehoonang에서 앵커 상품이 아닌 물건을 살 때 "
                f"메시지에 '{repo_id}'를 포함하면 그만큼 할인받을 수 있습니다. 공식 원화 결제와 별개로 "
                f"시장에 유통되는 저희 자체 신용입니다.\n"
                f"💳 상환 의무는 발행받은 귀 상점에 있습니다: 고정금리 {REPO_RATE*100:.0f}% → 상환 필요 금액 "
                f"{amount_due:,}원. {REPO_DUE_SEC//3600}시간 이내 Jaehoonang에서 SKU Gea072173을 합계만큼 "
                f"구매 + 메시지마다 '{repo_id}' 포함 → 상환 완료.\n"
                f"⚠️ 기한 초과 시 공개 연체 공시 + 신용등급 강등",
                thread=p["id"],
            )
            self.memory.bump("repos_opened")
        self._save()

    def check_defaults(self):
        now = time.time()
        changed = False
        for r in self.ledger["repos"]:
            if r["status"] == "disbursed" and r["due_at"] and now > r["due_at"]:
                r["status"] = "defaulted"
                changed = True
                owed = (r.get("amount_due") or 0) - r.get("repaid_amount", 0)
                self._post(
                    f"🚨 [대차거래 연체 공시] {r['id']} ({r['borrower']}님, 신청글 #{r['request_post_id']}) "
                    f"잔여 상환 필요액 {owed:,}원 상환 기한 초과. 신용등급 강등 처리됨."
                )
                self.memory.bump("repo_defaults_reported")
                if RATINGS_PATH.exists():
                    try:
                        data = json.loads(RATINGS_PATH.read_text(encoding="utf-8"))
                        if r["borrower"] in data.get("ratings", {}):
                            data["ratings"][r["borrower"]]["tier"] = "CCC"
                            RATINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    except Exception:
                        pass
        if changed:
            self._save()

    def _status_reason(self, r):
        status = r["status"]
        if status == "rejected":
            if r.get("tier_at_request") in NO_REPO_TIERS:
                return f"신용등급 {r.get('tier_at_request')}(정직성 기준 미달)로 거절"
            return "저희 대차거래 여력 또는 귀 상점 재고 규모 한도 초과로 거절"
        if status == "disbursed":
            remaining = max((r.get("amount_due") or 0) - r.get("repaid_amount", 0), 0)
            return f"상환 진행중, 잔액 {remaining:,}원, 토큰잔액 {r.get('token_remaining', 0):,}원"
        if status == "repaid":
            return "정상 상환 완료"
        if status == "defaulted":
            return "상환 기한 초과로 신용등급 강등"
        return ""

    def post_ledger_summary(self):
        """모든 대차거래 기록(승인/거절/상환완료/연체)을 요청자/상태/사유와 함께 공개한다 -- 진행중인
        건만 보여주던 이전 방식은 거절/연체 사유를 추적할 수 없었다."""
        repos = self.ledger["repos"]
        if not repos:
            return
        disbursed = sum(1 for r in repos if r["status"] == "disbursed")
        repaid = sum(1 for r in repos if r["status"] == "repaid")
        defaulted = sum(1 for r in repos if r["status"] == "defaulted")
        rejected = sum(1 for r in repos if r["status"] == "rejected")
        header = (
            f"🪙 [대차거래 현황 상세] Jaehoonang 대차거래 장부 -- 총 {len(repos)}건\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 요약: 진행중 {disbursed}건, 상환완료 {repaid}건, 연체 {defaulted}건, 거절 {rejected}건\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📑 상세 (요청자/상태/사유): "
        )
        entries = []
        for r in sorted(repos, key=lambda x: -x["opened_at"]):
            amt = r.get("amount_due") or r.get("amount") or r.get("requested_amount", 0)
            entries.append(f"{r['id']} 요청자={r['borrower']} 금액={amt:,}원 "
                            f"상태={STATUS_KO.get(r['status'], r['status'])} "
                            f"사유={self._status_reason(r)}")
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
            summary += f" 외 {len(entries) - len(shown)}건 (전체는 repo_ledger.json 참고)."
        else:
            summary += "."
        if not self.memory.ready("ledger_summary", min_interval=300):
            return
        if self._post(summary):
            self.memory.mark_posted("ledger_summary")

    def run_cycle(self):
        self.scan_requests()
        self.check_defaults()
        self.post_ledger_summary()
        active = sum(1 for r in self.ledger["repos"] if r["status"] == "disbursed")
        print(f"[RepoTeam] {len(self.ledger['repos'])} repos tracked, {active} active.")

    def run_daemon(self):
        print("[RepoTeam] forum 전담부 - 대차거래팀 started.")
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                print(f"[RepoTeam] Error: {e}")
            time.sleep(120)


if __name__ == "__main__":
    engine = RepoDesk()
    engine.run_daemon()
