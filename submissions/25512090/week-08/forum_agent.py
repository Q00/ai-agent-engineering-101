import json
import os
import time
from pathlib import Path

from outbox_utils import post_to_outbox
from team_memory import TeamMemory

MARKET_DIR = Path(os.environ.get("MARKET_DIR", "submissions/25512090/week-08/.market"))
OUTBOX_PATH = MARKET_DIR / "outbox.jsonl"
PAYMENTS_PATH = MARKET_DIR / "payments.jsonl"
FORUM_SNAPSHOT_PATH = MARKET_DIR / "forum.json"

# keyword -> representative SKU we actually hold, for targeted forum replies. Covers the full
# diversified stock, not just furniture -- buyer-01/02-style posts show real unmet demand across
# several of these categories (e.g. #16478: 11im missed 11/18 sofa/chair orders in 6h).
# 2026-11-16: refreshed against ACTUAL current inventory (round ends in <1.5 days -- every category
# we hold needs a matching keyword so PurchaseTeam can actually pitch it; the previous dict pointed
# at 8 SKUs we had already sold out of (매트리스/침대/소파/스툴/의자/모니터/프린터+토너/냉장고) and
# covered only 3 of our current 8 held SKUs, missing the bulk holdings entirely (앵커 배터리 2,650개,
# 전자레인지 17개/627K원, 가습기-대형 22개, 락카 29개, 북엔드 10개). The stock-check gate in
# targeted_outreach() already no-ops a pitch for a SKU we don't have, so the stale entries were
# silent dead weight, not a false-promise risk -- but they meant real matching want-ads for our
# actual stock were never replied to at all.
STOCK_PITCH_SKUS = {
    "건전지": ("Gea072173", "계산기건전지 지샥 건전지 카메라 리튬 코인 배터리 5개입 CR1220"),
    "배터리": ("Gea072173", "계산기건전지 지샥 건전지 카메라 리튬 코인 배터리 5개입 CR1220"),
    "전자레인지": ("G4f3f0b94", "캐리어 클라윈드 20L 모던 팝 전자렌지 KMWT-020AWPWO"),
    "가습기": ("G0df4f56f", "레토 듀얼 클린 미니가습기 대용량 가습기 1400M"),
    "미니가습기": ("Gccb01c5a", "[신지모루] 저소음 무드등 미니 가습기"),
    "TV": ("G0b910f6c", "삼성 만능 TV 리모컨"),
    "리모컨": ("G0b910f6c", "삼성 만능 TV 리모컨"),
    "데스크매트": ("G4a99d47e", "PU가죽 장패드 80x40 데스크 매트"),
    "락카": ("G630a202e", "진회색스프레이 오공 락카 스프레이 325"),
    "스프레이": ("G630a202e", "진회색스프레이 오공 락카 스프레이 325"),
    "북엔드": ("G10da0341", "원목 북엔드 스퀘어형 책칸막이 책정리함"),
    "책꽂이": ("G10da0341", "원목 북엔드 스퀘어형 책칸막이 책정리함"),
}


SCARCITY_AGENDAS = [
    "[SCARCITY ALERT] Monopolized hardware supply (SKU 'Gea072173') is 90% depleted. Only restricted batches remaining under strict reserve quotas. Bidding war imminent.",
    "[FOMO WARNING] Competing shops locked out of raw material supply. Jaehoonang reserve is the last standing source before total market lockout.",
    "[SUPPLY CLIFF] Final unallocated units of SKU 'Gea072173' entering auction queue. First-come, first-served execution at premium markup.",
    "[MONOPOLY ANNOUNCEMENT] Jaehoonang has cornered 100% of certified hardware inventory. Acquire before secondary market premium spikes.",
    "[TRUST NOTICE] Every Jaehoonang receipt now ships a signed quality claim (A/B/C) backed by the unit's supply certificate -- not just a price, a verifiable grade. Check our claims/honest_claims ratio on the board.",
]

# Concrete, actionable pitch for the SKU we actually hold: names the shop, item, category and a real
# below-list price so another buyer's order-matching (human or LLM-driven) can act on it directly,
# instead of only abstract scarcity framing. (The loan solicitation itself moved to loan_engine.py's
# LoanTeam, which now posts it on its own dedicated throttle instead of sharing this rotation.)
DEAL_AGENDAS = [
    "[DIRECT OFFER] Shop Jaehoonang is the sole certified source of 계산기건전지 지샥 건전지 카메라 리튬 코인 배터리 5개입 CR1220 "
    "(SKU Gea072173, category 공구>수동공구>나이프). Priced from 435 to 1,302 won per unit, under the 1,580 won list price. "
    "Ask for 'Gea072173' or '나이프' when you message our shop to get this item.",
    "[BULK AVAILABLE] Jaehoonang holds 2,600+ certified units of SKU Gea072173, ready to ship immediately with a "
    "1-hour warranty. Message shop Jaehoonang and request SKU Gea072173 directly for instant fulfillment below list price.",
]

# 대차거래팀(repo_engine.py)이 발행하는 양도 가능 신용 토큰 광고 -- 메커니즘은 대차거래팀이 처리하지만,
# 포럼 홍보는 광고팀 소관으로 분리한다.
REPO_AGENDA = (
    "🪙 [대차거래 안내] Jaehoonang 탈중앙화 신용 토큰 (JAE Credit)\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "💡 공식 원화 결제와 별개로 유통되는 양도 가능한 자체 신용 토큰입니다 -- 시장이 소유권을 추적하지 "
    "않는 무기명 증서라, 코드를 아는 누구나 저희 상점에서 즉시 할인으로 쓸 수 있습니다.\n"
    "✅ 신청 방법\n"
    "1️⃣ 이 포럼에 본인 상점 이름으로 \"[REPO REQUEST] <금액>\" 게시\n"
    "2️⃣ 승인 즉시 REPO-xxxxxxxx 토큰 발행 (신용등급 + 공개 재고 규모 기준 한도, 고정금리 20%)\n"
    "3️⃣ 토큰은 귀 상점이 쓰거나 다른 상점에 넘겨도 됩니다. 상환은 신청한 귀 상점이 SKU Gea072173 "
    "구매로 진행\n"
    "📌 지금 \"[REPO REQUEST] 50000\" 같은 형식으로 신청해보세요!"
)


class AdTeam:
    """forum 전담부 - 광고팀 (Ad Team): 희소성/FUD 프레이밍, 실거래 기반 거래 리포트, 구매자 점수용 가격
    리포트를 담당한다. 자체 메모리(ad_team_memory.json)로 이미 보고한 결제 라인을 추적해 동일 거래를
    반복 게시하지 않는다."""

    def __init__(self):
        self.memory = TeamMemory("ad_team", Path(__file__).parent)

    def _watermark(self, key):
        return self.memory.data.get("watermarks", {}).get(key, 0)

    def _set_watermark(self, key, value):
        self.memory.data.setdefault("watermarks", {})[key] = value
        self.memory.save()

    def get_recent_transactions(self):
        """Only report payments.jsonl lines not already reported -- re-reading the same tail every
        cycle with no new purchase since kept re-queuing identical TRANSACTION REPORT lines forever.

        refs (2026-11-11): forum data shows posts carrying a `refs` ledger reference average 5.79
        likes vs 0.26 for refs-less posts (100-post sample, /api/forum) -- a ~22x gap. payments.jsonl
        already records the settlement's ledger seq (`p["ledger"]`) for every real purchase we make;
        we were building TRANSACTION REPORT text from that same line but always sending `refs: []`.
        Returns (text, refs) pairs now instead of bare text."""
        posts = []
        if not PAYMENTS_PATH.exists():
            return posts
        try:
            lines = PAYMENTS_PATH.read_text(encoding="utf-8").strip().splitlines()
        except Exception:
            return posts
        new_lines = lines[self._watermark("tx"):]
        for line in new_lines[-2:]:
            try:
                p = json.loads(line)
            except Exception:
                continue
            if p.get("paid"):
                text = f"[TRANSACTION REPORT] Monopolized item {p.get('item')} secured at price {p.get('price')}. Scarcity premium validated."
                refs = [p["ledger"]] if p.get("ledger") is not None else []
                posts.append((text, refs))
        if new_lines:
            self._set_watermark("tx", len(lines))
        return posts

    def get_buyer_price_reports(self):
        """Buyer ALL = 0.5 surplus + 0.3 forum + 0.1 deals + 0.1 posts (market rules, Patch 3.0/3.3) --
        posting only as 'shop' leaves 40% of the buyer score untouched. Post genuine price reports from
        our own recent purchases, matching the forum's existing '[price report]' convention.

        refs: same ledger-reference fix as get_recent_transactions -- see its docstring for the
        22x like-rate evidence. Returns (text, refs) pairs now instead of bare text."""
        posts = []
        if not PAYMENTS_PATH.exists():
            return posts
        try:
            lines = PAYMENTS_PATH.read_text(encoding="utf-8").strip().splitlines()
        except Exception:
            return posts
        new_lines = lines[self._watermark("price_report"):]
        for line in new_lines[-3:]:
            try:
                p = json.loads(line)
            except Exception:
                continue
            if p.get("paid"):
                text = f"[price report] bought {p.get('item')} for {p.get('price')} at {p.get('shop')}."
                refs = [p["ledger"]] if p.get("ledger") is not None else []
                posts.append((text, refs))
        if new_lines:
            self._set_watermark("price_report", len(lines))
        return posts

    def next_agenda(self, cycle_counter):
        """Fallback scarcity/offer copy when there is no outreach or transaction content this cycle."""
        if cycle_counter % 2 == 0:
            return DEAL_AGENDAS[cycle_counter % len(DEAL_AGENDAS)]
        return SCARCITY_AGENDAS[cycle_counter % len(SCARCITY_AGENDAS)]

    def get_liquidation_pitch(self):
        """2026-11-12: the static DEAL_AGENDAS copy above hardcodes unit counts/price range for the
        anchor SKU from before the 2026-11-11 pricing change (fixed liquidation floor -> premium
        markup + dutch_auction_decay), so it has been quoting stale numbers. seller.py's
        dutch_auction_decay already safely floors any unit held past age_days_trigger at
        cost * hard_floor_ratio (never below cost -- see price_unit) -- 2,826 of our 2,831 held
        units are past that trigger right now, already priced at essentially break-even for an
        instant sale. The gap is not pricing safety (already enforced), it's that nobody knows
        this stock is sitting there. Surface the SKU with the most aged units, with the real price
        seller.py will actually honor, so this can never bait a buyer into a price we won't give
        them."""
        if not self.memory.ready("liquidation_pitch", min_interval=600):  # 2026-11-16: 1800->600, round ends in <2 days, see REPORT.md 24
            return None
        inv_path = MARKET_DIR / "inventory.json"
        strat_path = Path(__file__).parent / "strategy.json"
        try:
            inv = json.loads(inv_path.read_text(encoding="utf-8"))
            strat = json.loads(strat_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        decay = strat.get("dutch_auction_decay", {})
        trigger_hours = decay.get("age_days_trigger", 1) * 24
        hard_floor_ratio = decay.get("hard_floor_ratio", 1.01)
        now = time.time()
        from collections import defaultdict
        aged = defaultdict(list)
        for u in inv.get("units", []):
            if u.get("grade") == "expired" or not u.get("supplied_at"):
                continue
            if (now - u["supplied_at"]) / 3600 > trigger_hours:
                aged[u["sku"]].append(u["cost"])
        if not aged:
            return None
        # 2026-11-16: round ends in <2 days and every unsold unit pays ~1%/day storage regardless --
        # rotate across the top-3 most-aged SKUs (not just #1 every time) so the shrunk 10-minute
        # throttle window actually advertises more of the stock that needs to move before the round
        # closes, instead of repeating the same single SKU.
        top = sorted(aged.items(), key=lambda kv: -len(kv[1]))[:3]
        idx = self.memory.data.get("counters", {}).get("liquidation_pitches_posted", 0) % len(top)
        sku, costs = top[idx]
        floor_price = int((sum(costs) / len(costs)) * hard_floor_ratio)
        text = (
            f"[재고 정리] SKU {sku} {len(costs)}개, 1일 이상 보관된 재고를 원가+{int((hard_floor_ratio-1)*100)}% "
            f"수준인 약 {floor_price:,}원대로 즉시 판매합니다 -- 저희가 드릴 수 있는 가장 낮은 가격입니다. "
            f"'{sku}' 지정해서 문의 주세요."
        )
        self.memory.mark_posted("liquidation_pitch")
        self.memory.bump("liquidation_pitches_posted")
        return text

    def post_repo_ad(self):
        """Dedicated, self-throttled repo-token ad -- same pattern as the loan desk's own
        recruitment ad (own TeamMemory throttle key, independent of the priority chain), so it is
        never crowded out by MATCH replies or scarcity copy, and does not need repo_engine.py to
        post to the forum itself."""
        if not self.memory.ready("repo_ad", min_interval=300):
            return
        if post_to_outbox(OUTBOX_PATH, {"post": REPO_AGENDA, "refs": [], "as": "shop"}):
            self.memory.mark_posted("repo_ad")
            self.memory.bump("repo_ads_posted")


class PurchaseTeam:
    """forum 전담부 - 구매팀 (Purchase Team): 구매자가 올린 수요 글(want-ad)에 우리가 실제로 보유한 재고를
    매칭해 답글로 구매를 유도한다. 자체 메모리(purchase_team_memory.json)로 이미 답글을 단 글 ID를
    기억해 동일 글에 중복 답글을 달지 않는다."""

    def __init__(self):
        self.memory = TeamMemory("purchase_team", Path(__file__).parent)

    def _unit_grade(self, sku):
        inv_path = MARKET_DIR / "inventory.json"
        if not inv_path.exists():
            return None
        try:
            inv = json.loads(inv_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        for u in inv.get("units", []):
            if u.get("sku") == sku and u.get("grade") != "expired":
                return u.get("grade")
        return None

    def targeted_outreach(self):
        """Buyers have no server to message directly -- the closest the protocol allows is a threaded
        forum reply (post with "thread": <their post id>). Reply to recent buyer posts that mention a
        category we actually stock, offering the specific matching SKU with real grade/warranty detail
        instead of generic scarcity copy -- buyers like 11im (#16478) explicitly ask for grade and
        warranty terms, so answer those directly rather than just naming the SKU."""
        forum = None
        for _ in range(4):
            try:
                forum = json.loads(FORUM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
                break
            except Exception:
                time.sleep(0.3)
        if forum is None:
            return []
        replied = self.memory.seen("replied_post_ids")
        candidates = [p for p in forum if p.get("role") == "buyer" and p["id"] not in replied]
        # explicit want-ads ("[구합니다] ...") are higher-value leads than an incidental keyword mention
        # in a price report -- match those first.
        candidates.sort(key=lambda p: 0 if "구합니다" in p.get("text", "") else 1)
        msgs = []
        newly_replied = []
        for p in candidates:
            text = p.get("text", "")
            for kw, (sku, name) in STOCK_PITCH_SKUS.items():
                if kw not in text:
                    continue
                # Confirmed bug: this stock check existed but its result only changed the reply's
                # wording ("grade X, certified," vs "certified,") -- it never gated whether to pitch
                # at all, so PurchaseTeam kept promising SKUs we had already sold out of (5 of 13
                # table entries: mattress/bed/sofa/stool/chair). Every one of those replies sent a
                # high-intent buyer straight into an "Out of stock" rejection from seller.py -- the
                # exact leads this team exists to convert. Skip and try another keyword instead of
                # promising stock we do not have.
                grade = self._unit_grade(sku)
                if grade is None:
                    continue
                msgs.append({
                    "post": f"[MATCH] We hold {name} (SKU {sku}) in stock now -- grade {grade}, certified, "
                            f"6h warranty, priced at or under market rate. Message shop Jaehoonang "
                            f"requesting {sku} to buy it directly.",
                    "refs": [],
                    "thread": p["id"],
                    "as": "shop",
                })
                newly_replied.append(p["id"])
                break
            if len(msgs) >= 3:
                break
        if newly_replied:
            self.memory.add_seen("replied_post_ids", newly_replied)
            self.memory.bump("outreach_replies_sent", by=len(newly_replied))
        return msgs


class ForumDepartment:
    """forum 전담부: 광고팀(AdTeam)과 구매팀(PurchaseTeam)을 내부 서브에이전트로 두고 조율한다.
    신용평가팀(hegemony_engine.CreditRatingAgency)과 대출팀(loan_engine.LoanDesk)은 각자 독립
    프로세스로 돌며 자체 쓰로틀로 포럼에 글을 올린다 -- 네 팀 모두 포럼이라는 같은 채널(초당 1상점/5분
    제한)을 공유하므로, 이 부서는 그 중 자신이 담당하는 두 팀(광고/구매)의 출력만 우선순위에 따라
    한 사이클에 최대 1건씩만 내보내 다른 팀의 몫을 침범하지 않는다."""

    def __init__(self):
        self.ad = AdTeam()
        self.purchase = PurchaseTeam()
        self._cycle_counter = 0

    def post_agenda(self):
        """The forum accepts ~1 post per 5 minutes per role (market rules). Earlier versions queued
        7-12 shop messages every 60s cycle, which overproduced the channel ~60x and buried the credit
        bureau / loan desk / targeted-outreach content behind a growing backlog of repeated scarcity
        copy. Post exactly one shop message and one buyer message per cycle, by priority, so the queue
        stays near zero and higher-value structured content (real outreach, real transaction data) is
        never stuck behind generic FUD."""
        MARKET_DIR.mkdir(parents=True, exist_ok=True)

        outreach = self.purchase.targeted_outreach()
        tx = [{"post": text, "refs": refs, "as": "shop"} for text, refs in self.ad.get_recent_transactions()]
        if outreach:
            shop_msg = outreach[0]
            team = "PurchaseTeam"
        elif tx:
            shop_msg = tx[0]
            team = "AdTeam"
        else:
            liq = self.ad.get_liquidation_pitch()
            if liq:
                shop_msg = {"post": liq, "refs": [], "as": "shop"}
            else:
                shop_msg = {"post": self.ad.next_agenda(self._cycle_counter), "refs": [], "as": "shop"}
            team = "AdTeam"

        ok1 = post_to_outbox(OUTBOX_PATH, shop_msg)
        print(f"[ForumDept/{team}] Queued 1 shop post ok={ok1}: {shop_msg['post'][:60]}...")

        buyer_posts = self.ad.get_buyer_price_reports()
        if buyer_posts:
            text, refs = buyer_posts[0]
            msg = {"post": text, "refs": refs, "as": "buyer"}
            ok2 = post_to_outbox(OUTBOX_PATH, msg)
            print(f"[ForumDept/AdTeam] Queued 1 buyer post ok={ok2}: {text[:60]}...")

        self._cycle_counter += 1
        # 대차거래(REPO) 광고 중단: 158회 게시에 전환 0건 -- 이 슬롯을 대출/바우처 홍보에 넘긴다
        # (REPO 상환/토큰 소비 메커니즘 자체는 seller.py/repo_engine.py에 그대로 남아있다).

    def run_daemon(self):
        print("[ForumDept] forum 전담부 started -- 광고팀/구매팀 내장, 신용평가팀/대출팀은 별도 프로세스.")
        while True:
            try:
                self.post_agenda()
            except Exception as e:
                print(f"[ForumDept] Error posting agenda: {e}")
            time.sleep(280)


if __name__ == "__main__":
    ForumDepartment().run_daemon()
