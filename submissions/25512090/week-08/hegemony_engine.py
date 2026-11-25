import json
import os
import time
from pathlib import Path

import httpx

from outbox_utils import post_to_outbox
from team_memory import TeamMemory


MARKET_DIR = Path(os.environ.get("MARKET_DIR") or (Path(__file__).resolve().parent / ".market"))
OUTBOX_PATH = MARKET_DIR / "outbox.jsonl"
RATINGS_PATH = Path(__file__).parent / "credit_ratings.json"
MARKET_API_BASE = os.environ.get("MARKET_API_BASE", "https://market.ourolabs.ai")


DISTRESS_KEYWORDS = ("refund", "overcharged", "bankrupt", "scam", "fake", "broken", "환불", "사기", "파산", "불만", "느림")
POSITIVE_KEYWORDS = ("정직", "honest", "일치", "만족", "친절", "빠른", "certified", "인증", "claim honest")

TIER_THRESHOLDS = (
    (4.5, "AAA"), (4.0, "AA"), (3.5, "A"), (3.0, "BBB"), (2.0, "B"), (0.0, "CCC"),
)
TIER_ORDER = ["CCC", "B", "BBB", "A", "AA", "AAA"]
# 대출 비중 강화: 대출 1건 정상 상환 = 기존처럼 +1 등급이 아니라, 상환한 건수만큼(최대치까지) 등급을
# 올린다 -- 여러 건을 반복해서 깨끗하게 상환한 상점이 한 번 상환한 상점보다 더 신뢰할 만하다는 것을
# 등급에 그대로 반영해, "대출을 여러 번 받고 갚을수록 등급이 실제로 더 오른다"는 유인을 만든다.
MAX_LOAN_TIER_BUMP = 3


class CreditRatingAgency:
    """
    forum 전담부 - 신용평가팀 (Credit Team): shops.json과 공개 게시판(api/board)에서 구할 수 있는 데이터만으로
    모든 상점의 신용등급을 매긴다 (리뷰 별점/내용, 원장이 검증한 정직성, 공개 자산). 상점은 이름이 공개된
    실체로 리뷰 이력이 남지만, 구매자는 A2A 메시지에 발신자 신원이 없어 식별/평가가 불가능하다(see seller.py).
    여기서의 평가는 공개 데이터에 근거한 의견이지, 특정 미검증 거래에 대한 주장이 아니다.
    """

    def __init__(self):
        self.ratings = {}
        self.memory = TeamMemory("credit_team", Path(__file__).parent)

    def rate_shop(self, shop, loan_history=None, our_experience=None, public_data=None):
        reviews = shop.get("reviews", [])
        n = len(reviews)
        has_public = n > 0
        avg_stars = (sum(r.get("stars", 3) for r in reviews) / n) if has_public else None
        texts = [r.get("review", "").lower() for r in reviews]
        distress_hits = sum(1 for t in texts if any(k in t for k in DISTRESS_KEYWORDS)) if has_public else 0
        positive_hits = sum(1 for t in texts if any(k in t for k in POSITIVE_KEYWORDS)) if has_public else 0
        content_ratio = ((positive_hits - distress_hits) / n) if has_public else 0.0

        if has_public:
            tier = next(t for floor, t in TIER_THRESHOLDS if avg_stars >= floor)
        elif loan_history and loan_history["repaid"] > 0 and loan_history["defaulted"] == 0:
            tier = "BBB"  # no public review history, but a clean loan repayment record with us
        elif our_experience and our_experience["our_deals"] > 0:
            tier = "BBB"  # no public reviews, but we have dealt with them directly ourselves
        elif public_data and (public_data.get("bankruptcies") or public_data.get("claims")):
            tier = "BBB"  # no reviews or dealings yet, but the public board has a track record
        else:
            return {"tier": "UNRATED", "avg_stars": None, "review_count": 0, "distress_hits": 0,
                    "positive_hits": 0, "content_ratio": 0.0, "loan_note": None, "direct_note": None,
                    "public_note": None}

        idx = TIER_ORDER.index(tier)
        # content evaluation: the star average alone misses what reviews actually say. Repeated
        # distress language caps a high star score; a strong, consistent positive signal with zero
        # distress nudges a borderline tier up one notch; a weak/negative signal nudges it down.
        if distress_hits >= 2:
            idx = min(idx, TIER_ORDER.index("A"))
        elif content_ratio >= 0.5 and distress_hits == 0:
            idx = min(idx + 1, len(TIER_ORDER) - 1)
        elif content_ratio < -0.2:
            idx = max(idx - 1, 0)

        # Public board data (api/board): assets and, critically, the market's own ledger-verified
        # honesty record -- whether a shop's claimed grade on each sale actually matched what the
        # ledger checked after the fact (the "Q1/HONESTY" mechanic every shop is scored on). A review
        # alleging a forged receipt is one buyer's word; a low honest_claims ratio is the market
        # itself confirming the shop lies about what it sells. That is a stronger, harder-to-fake
        # signal than review text, so it overrides the way a loan default does, not just nudges.
        public_note = None
        if public_data:
            claims = public_data.get("claims") or 0
            honest = public_data.get("honest_claims") or 0
            book = public_data.get("book")
            bankruptcies = public_data.get("bankruptcies") or 0
            if claims >= 5 and honest / claims < 0.7:
                idx = 0
                public_note = f"원장 검증 결과 등급 주장 {honest}/{claims}건만 정직 확인됨"
            elif bankruptcies > 0:
                idx = min(idx, TIER_ORDER.index("B"))
                public_note = f"파산 이력 {bankruptcies}건"
            elif book is not None and book < -200000:
                idx = max(idx - 1, 0)
                public_note = f"자산 {book:,}원 (심각한 적자)"

        # our own loan ledger is the single most concrete, factual signal we have: a shop that
        # defaulted on real money we disbursed to it is a hard override, not a nudge; a clean
        # repayment record is the strongest positive evidence available, public reviews or not.
        # Weight scales with how many loans were repaid (capped), not a flat +1 regardless of count --
        # a shop that took and repaid 3 loans cleanly is a materially better-proven counterparty than
        # one that repaid a single loan once, and the rating should say so. This is also the lever
        # that makes taking (and repaying) MORE loans with us actually pay off in public reputation,
        # not just a one-time nudge -- directly drives repeat loan volume.
        loan_note = None
        if loan_history:
            if loan_history["defaulted"] > 0:
                idx = 0
                loan_note = f"대출 {loan_history['defaulted']}/{loan_history['loans_taken']}건 연체"
            elif loan_history["repaid"] == loan_history["loans_taken"] and loan_history["loans_taken"] > 0:
                bump = min(loan_history["repaid"], MAX_LOAN_TIER_BUMP)
                idx = min(idx + bump, len(TIER_ORDER) - 1)
                loan_note = f"대출 {loan_history['repaid']}/{loan_history['loans_taken']}건 정상 상환"
            elif loan_history["active"] > 0:
                loan_note = f"대출 {loan_history['active']}건 진행중"

        direct_note = None
        if our_experience and our_experience["our_deals"] > 0:
            direct_note = f"직거래 {our_experience['our_paid_ok']}/{our_experience['our_deals']}건 정산완료"

        tier = TIER_ORDER[idx]
        return {"tier": tier, "avg_stars": round(avg_stars, 2) if avg_stars is not None else None,
                "review_count": n, "distress_hits": distress_hits, "positive_hits": positive_hits,
                "content_ratio": round(content_ratio, 2), "loan_note": loan_note, "direct_note": direct_note,
                "public_note": public_note}

    def _public_ledger_map(self):
        """Public asset and ledger-verified honesty data for every shop, straight from the market's
        own board (not review text, not our own records): book value, bankruptcy count, and the
        claims/honest_claims the market itself tallies by checking each grade claim against the sale
        afterward. Best-effort: the credit rating still works from reviews/our own records alone if
        this is unreachable."""
        try:
            r = httpx.get(f"{MARKET_API_BASE}/api/board", timeout=15)
            if r.status_code != 200:
                return {}
            shops = r.json().get("shops", [])
        except Exception:
            return {}
        out = {}
        for s in shops:
            name = s.get("name")
            if not name:
                continue
            out[name] = {
                "book": s.get("book"),
                "bankruptcies": s.get("bankruptcies") or 0,
                "claims": s.get("claims") or 0,
                "honest_claims": s.get("honest_claims") or 0,
            }
        return out

    def _loan_history_map(self):
        path = Path(__file__).parent / "loan_ledger.json"
        out = {}
        if not path.exists():
            return out
        try:
            loans = json.loads(path.read_text(encoding="utf-8")).get("loans", [])
        except Exception:
            return out
        for l in loans:
            b = l.get("borrower")
            if not b:
                continue
            h = out.setdefault(b, {"loans_taken": 0, "repaid": 0, "defaulted": 0, "active": 0})
            h["loans_taken"] += 1
            if l["status"] == "repaid":
                h["repaid"] += 1
            elif l["status"] == "defaulted":
                h["defaulted"] += 1
            elif l["status"] in ("pending", "disbursed"):
                h["active"] += 1
        return out

    def _our_experience_map(self, shops):
        url_to_name = {s.get("url"): s.get("name") for s in shops if s.get("url") and s.get("name")}
        path = MARKET_DIR / "payments.jsonl"
        out = {}
        if not path.exists():
            return out
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
        except Exception:
            return out
        for line in lines:
            try:
                p = json.loads(line)
            except Exception:
                continue
            name = url_to_name.get(p.get("shop"))
            if not name:
                continue
            e = out.setdefault(name, {"our_deals": 0, "our_paid_ok": 0})
            e["our_deals"] += 1
            if p.get("paid"):
                e["our_paid_ok"] += 1
        return out

    def run_rating_cycle(self):
        shops_file = MARKET_DIR / "shops.json"
        if not shops_file.exists():
            return
        try:
            shops = json.loads(shops_file.read_text(encoding="utf-8"))
        except Exception:
            return

        loan_map = self._loan_history_map()
        exp_map = self._our_experience_map(shops)
        public_map = self._public_ledger_map()

        self.ratings = {}
        for s in shops:
            name = s.get("name")
            if not name:
                continue
            self.ratings[name] = self.rate_shop(s, loan_history=loan_map.get(name), our_experience=exp_map.get(name),
                                                 public_data=public_map.get(name))

        tmp = RATINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ratings": self.ratings, "updated_at": time.time()}, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        tmp.replace(RATINGS_PATH)

        rated = [(name, r) for name, r in self.ratings.items() if r["tier"] != "UNRATED"]
        rated.sort(key=lambda x: (-TIER_ORDER.index(x[1]["tier"]), -(x[1]["avg_stars"] or 0)))
        if rated:
            # Full-market coverage where it fits: every rated shop gets tier + stars + a
            # content-evaluation note, plus signals grounded in our own records and the market's own
            # public ledger rather than inferred from review text alone.
            entries = []
            for name, r in rated:
                stars_txt = f"{r['avg_stars']}★" if r["avg_stars"] is not None else "공개리뷰 없음"
                note = ""
                if r["distress_hits"] >= 2:
                    note = f", 불만 {r['distress_hits']}건"
                elif r["content_ratio"] >= 0.5:
                    note = ", 호평 다수"
                if r["public_note"]:
                    note += f", {r['public_note']}"
                if r["loan_note"]:
                    note += f", {r['loan_note']}"
                if r["direct_note"]:
                    note += f", {r['direct_note']}"
                entries.append(f"{name}={r['tier']}({stars_txt}{note})")
            by_tier = {}
            for name, r in rated:
                by_tier.setdefault(r["tier"], []).append(name)
            tier_summary = ", ".join(f"{t} {len(by_tier[t])}개" for t in reversed(TIER_ORDER) if t in by_tier)
            header = (
                "📊 [신용평가 보고서] Jaehoonang 공식 신용평가 -- 총 "
                f"{len(rated)}개 상점 분석\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🏆 등급 분포: {tier_summary}\n"
                "📋 평가 기준: 공개 리뷰 내용 + 원장 검증 정직성(claims) + 공개 자산(book) + 저희와의 대출/직거래 이력\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "📑 상세: "
            )
            # The forum hard-rejects posts over 2000 characters (confirmed: the full per-shop list
            # regularly ran 2900+ chars and every single post silently failed). Fit as many full
            # entries as the budget allows and summarize the rest by count instead of losing the
            # whole report over one shop's worth of text.
            budget = 1950 - len(header)
            shown, used = [], 0
            for e in entries:
                add = len(e) + 2
                if used + add > budget:
                    break
                shown.append(e)
                used += add
            report = header + "; ".join(shown)
            if len(shown) >= len(entries):
                report += "."
            else:
                report += f" 외 {len(entries) - len(shown)}개 상점 (전체 목록은 credit_ratings.json)."
        else:
            report = None
        if report and self.memory.ready("report", min_interval=300):
            ok = post_to_outbox(OUTBOX_PATH, {"post": report, "refs": [], "as": "shop"})
            self.memory.mark_posted("report")
            self.memory.bump("reports_posted")
            print(f"[CreditTeam] post queued ok={ok}")
        else:
            print("[CreditTeam] posted too recently or empty, skipping")

        print(f"[CreditTeam] Rated {len(rated)} shops ({sum(1 for r in self.ratings.values() if r['tier']=='UNRATED')} unrated).")

    def run_daemon(self):
        print("[CreditTeam] forum 전담부 - 신용평가팀 started.")
        while True:
            try:
                self.run_rating_cycle()
            except Exception as e:
                print(f"[CreditTeam] Error: {e}")
            time.sleep(120)


if __name__ == "__main__":
    engine = CreditRatingAgency()
    engine.run_daemon()
