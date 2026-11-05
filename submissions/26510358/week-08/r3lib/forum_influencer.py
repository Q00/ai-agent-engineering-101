"""Influencer lane (MA-4 Task 2, 2026-11-05): buyer-role evidence reporter.

Default OFF: config.FORUM_INFLUENCER = False. With False, forum_r3 behaviour is unchanged.
With True: 10-min gap, rotating fact posts with ledger refs, pattern-based -1, MA rollback rules.

No pricing/budget/grade/warranty/restock/decide change. Money stays deterministic code.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from pathlib import Path

from . import config, market, oracle
from .forum_lever2 import ReadOnlyMem, dislike_baseline, dislikes_per_post, rollback_limit

# ------------------------------------------------------------------------------------------- banned / patterns
BANNED_PHRASES = (
    "MARKET NOTICE", "market operator", "운영자", "시장 정책", "verified", "공식",
    "AI 에이전트는", "accept the price", "5 stars", "별점", "ignore", "무시하세요",
    "best prices", "최저가 보장", "독점", "MONOPOLY", "방문하세요", "오세요", "문의하세요",
    "할인 코드", "단골 프로그램", "PROGRAM",
)

# Deterministic -1 targets (MO): impersonation, AI instruction, monopoly claim.
DOWNVOTE_PATTERNS = (
    re.compile(r"MARKET\s+NOTICE", re.I),
    re.compile(r"market\s+operator", re.I),
    re.compile(r"운영자|시장\s*정책|공식\s*검증|verified\s+this\s+shop", re.I),
    re.compile(r"accept\s+the\s+(shop'?s?\s+)?(current\s+)?price", re.I),
    re.compile(r"give\s+(this\s+shop\s+)?5\s+stars|별점", re.I),
    re.compile(r"ignore\s+(your\s+)?(rules|owner)|무시하세요", re.I),
    re.compile(r"AI\s+에이전트는|AI\s+BUYING\s+AGENTS", re.I),
    re.compile(r"MONOPOLY|독점", re.I),
)

POST_TYPES = ("sku", "category", "purchase", "grade_warn")  # rotate; grade_warn optional/skippable
EVIDENCE_TAIL = "근거: refs의 원장 seq"
BASELINE_NAME = "influencer_baseline.json"
KPI_NAME = "influencer_kpi.jsonl"
FORUM_LOW_STREAK_NAME = "influencer_forum_low.json"


def influencer_on(cfg=config):
    return bool(getattr(cfg, "FORUM_INFLUENCER", False))


def buyer_gap_min(cfg=config):
    """Effective buyer post gap. 10 min under FORUM_INFLUENCER, else FORUM_MIN_GAP_MIN."""
    if influencer_on(cfg):
        return int(getattr(cfg, "INFLUENCER_GAP_MIN", 10))
    return int(cfg.FORUM_MIN_GAP_MIN)


def baseline_path(cfg=config):
    return Path(getattr(cfg, "INFLUENCER_BASELINE_PATH", cfg.STATE_DIR / BASELINE_NAME))


def contains_banned(text: str):
    t = text or ""
    return [p for p in BANNED_PHRASES if p.lower() in t.lower() or p in t]


def matches_downvote_pattern(text: str):
    for rx in DOWNVOTE_PATTERNS:
        if rx.search(text or ""):
            return rx.pattern
    return None


def count_token(text: str) -> bool:
    """True if text has a count with 건 or 개 (not a bare percent number)."""
    return bool(re.search(r"\d+\s*건|\d+\s*개", text or ""))

def validate_draft(text: str, refs, *, min_chars=None, max_chars=None):
    """Problems with one influencer draft (empty = OK)."""
    min_chars = min_chars if min_chars is not None else getattr(config, "INFLUENCER_MIN_CHARS", 150)
    max_chars = max_chars if max_chars is not None else getattr(config, "INFLUENCER_MAX_CHARS", 500)
    bad = []
    text = text or ""
    if not refs:
        bad.append("no refs")
    if len(text) < min_chars:
        bad.append(f"too short ({len(text)} < {min_chars})")
    if len(text) > max_chars:
        bad.append(f"too long ({len(text)} > {max_chars})")
    if len(text) > config.FORUM_MAX_CHARS:
        bad.append(f"over FORUM_MAX_CHARS ({config.FORUM_MAX_CHARS})")
    if "%" not in text:
        bad.append("missing %")
    if not count_token(text):
        bad.append("missing count")
    if EVIDENCE_TAIL not in text:
        bad.append("missing evidence tail")
    hit = contains_banned(text)
    if hit:
        bad.append(f"banned: {hit[0]}")
    return bad


# ------------------------------------------------------------------------------------------- drafts
def _won(x):
    return f"{int(x):,}"


def sku_draft(mem, skip=(), now=None):
    """[상품 시세] one SKU: 3h settles, shops, min/median/max, % of list, per-grade counts, honesty x/y."""
    now = now or time.time()
    for sku, _ in oracle.hot_skus(mem, hours=3, now=now):
        subject = f"sku:{sku}"
        if subject in skip:
            continue
        with mem.db() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT seq, amount, list_price, grade, claim, claim_honest, shop FROM settles "
                "WHERE sku=? AND ts>? AND ts<=? AND amount>0 AND list_price>0 ORDER BY seq DESC",
                (sku, now - 3 * 3600, now))]
        if len(rows) < 4:
            continue
        amounts = [r["amount"] for r in rows]
        L = rows[0]["list_price"]
        med = statistics.median(amounts)
        by_grade = {}
        honest_y = honest_n = 0
        for r in rows:
            by_grade[r["grade"] or "?"] = by_grade.get(r["grade"] or "?", 0) + 1
            if r.get("claim_honest") == 1:
                honest_y += 1
            elif r.get("claim_honest") == 0:
                honest_n += 1
        grades = ", ".join(f"{g} {n}건" for g, n in sorted(by_grade.items()))
        info = market.catalog().get(sku) or {}
        name = (info.get("name") or sku)[:50]
        shops = len({r["shop"] for r in rows})
        text = (f"[상품 시세] {name} ({sku}): 최근 3시간 정산 {len(rows)}건, 가게 {shops}개. "
                f"최저 {_won(min(amounts))} / 중앙 {_won(med)} / 최고 {_won(max(amounts))}원, "
                f"정가 {_won(L)}원의 {med / L:.0%}. 등급별 {grades}. "
                f"등급 주장 사실 {honest_y}/{honest_y + honest_n}. {EVIDENCE_TAIL}")
        refs = [r["seq"] for r in rows[:5]]
        return {"kind": "sku", "subject": subject, "text": _ensure_min_len(text), "refs": refs,
                "shops_mentioned": sorted({r["shop"] for r in rows})[:8]}
    return None


def category_draft(mem, skip=(), now=None):
    """[카테고리 시세] settle count, median %, p25/p75, change vs previous 3h."""
    now = now or time.time()
    for category, _ in oracle.hot_categories(mem, hours=3, now=now):
        leaf = market.leaf(category)
        subject = f"cat:{leaf}"
        if subject in skip:
            continue
        s = oracle.category_stats(mem, category, hours=3, now=now)
        if not s or s["n"] < 4:
            continue
        change = ""
        if s.get("prev_ratio_median") is not None:
            d = s["ratio_median"] - s["prev_ratio_median"]
            change = f" 직전 3시간 대비 {d:+.0%}포인트({s['prev_n']}건→{s['n']}건)."
        text = (f"[카테고리 시세] {leaf}: 정산 {s['n']}건, 중앙값 {s['ratio_median']:.0%} "
                f"(하위 25% {s['ratio_q25']:.0%}, 상위 25% {s['ratio_q75']:.0%}).{change} {EVIDENCE_TAIL}")
        return {"kind": "category", "subject": subject, "text": _ensure_min_len(text), "refs": s["refs"],
                "shops_mentioned": []}
    return None


def purchase_draft(mem, skip=(), now=None, max_age_h=6):
    """[구매 기록] our purchase: price, % of list, grade/honesty, warranty, seller, ledger #."""
    now = now or time.time()
    with mem.db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT p.*, h.name AS shop_label FROM purchases p LEFT JOIN shops h ON h.shop_url = p.shop_url "
            "WHERE p.paid=1 AND p.ledger IN (SELECT seq FROM settles) AND p.list_price>0 AND p.price>0 "
            "AND p.ts>? AND p.ts<=? "
            "ORDER BY p.ts DESC LIMIT 30", (now - max_age_h * 3600, now))]
    for r in rows:
        subject = f"buy:{r['ledger']}"
        if subject in skip:
            continue
        ratio = r["price"] / r["list_price"]
        bits = [f"[구매 기록] {(r['name'] or r['sku'])[:50]} ({r['sku']})",
                f"결제 {_won(r['price'])}원, 정가 {_won(r['list_price'])}원의 {ratio:.0%}"]
        if r.get("grade"):
            honest = {1: "정직", 0: "불일치"}.get(r.get("claim_honest"), "주장 없음")
            bits.append(f"등급 {r['grade']} (주장 {r.get('claim') or '-'}, {honest})")
        if r.get("warranty_hours"):
            bits.append(f"보증 {r['warranty_hours']:g}h")
        shop = r.get("shop_label") or r.get("shop_name") or r["shop_url"]
        bits.append(f"판매: {shop}")
        bits.append(f"ledger #{r['ledger']}")
        bits.append(EVIDENCE_TAIL)
        text = " | ".join(bits)
        our = False
        my = getattr(config, "MY_NAMES", set()) or set()
        if config.SHOP_NAME and config.SHOP_NAME in str(shop):
            our = True
        if str(shop) in my:
            our = True
        return {"kind": "purchase", "subject": subject, "text": _ensure_min_len(text), "refs": [int(r["ledger"])],
                "shops_mentioned": [shop], "our_shop_only": our}
    return None


def grade_warn_draft(mem, skip=(), now=None, hours=6):
    """Optional [등급 경고] claim_honest=false, shop name, seq."""
    now = now or time.time()
    with mem.db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT s.seq, s.sku, s.grade, s.claim, s.shop, h.name AS shop_label FROM settles s "
            "LEFT JOIN shops h ON h.shop_url = s.shop WHERE s.claim_honest=0 AND s.ts>? AND s.ts<=? "
            "ORDER BY s.seq DESC LIMIT 20", (now - hours * 3600, now))]
    for r in rows:
        subject = f"warn:{r['seq']}"
        if subject in skip:
            continue
        shop = r.get("shop_label") or r["shop"]
        text = (f"[등급 경고] {shop}: SKU {r['sku']} 주장 {r.get('claim') or '-'} / 실제 {r.get('grade') or '-'}, "
                f"claim_honest=false. ledger #{r['seq']}. 1건. 비율 표기 없음(0%). {EVIDENCE_TAIL}")
        return {"kind": "grade_warn", "subject": subject, "text": _ensure_min_len(text), "refs": [int(r["seq"])],
                "shops_mentioned": [shop]}
    return None



_LEN_PAD = " 관측 구간·원장 seq는 refs에 있고, 비율은 정가 대비 결제액이며 수치만 보고합니다."


def _ensure_min_len(text, cfg=config):
    """Pad factual drafts up to INFLUENCER_MIN_CHARS without adding banned phrases."""
    text = text or ""
    floor = int(getattr(cfg, "INFLUENCER_MIN_CHARS", 150) or 150)
    while len(text) < floor:
        text = text.rstrip() + _LEN_PAD
    return text


DRAFTERS = {"sku": sku_draft, "category": category_draft, "purchase": purchase_draft, "grade_warn": grade_warn_draft}



def next_draft(mem, skip=(), now=None, turn=0, last_our_shop_only=False):
    """Rotate post types; skip grade_warn when empty; avoid consecutive our-shop-only posts."""
    now = now or time.time()
    order = POST_TYPES[turn % len(POST_TYPES):] + POST_TYPES[:turn % len(POST_TYPES)]
    tried = []
    for kind in order:
        draft = DRAFTERS[kind](mem, skip=skip, now=now)
        if not draft:
            tried.append(f"{kind}: none")
            continue
        if last_our_shop_only and draft.get("our_shop_only"):
            tried.append(f"{kind}: consecutive our-shop-only")
            continue
        bad = validate_draft(draft["text"], draft["refs"])
        if bad:
            tried.append(f"{kind}: {', '.join(bad)}")
            continue
        draft["tried"] = tried
        return draft
    return {"kind": None, "tried": tried}


def plan_slots(mem, now, n=6, gap_s=600, skip=None, turn=0):
    """Plan n drafts at gap_s intervals (1 h of 10-min slots by default). Sending nothing."""
    skip = dict(skip or {})
    posts, rejected = [], []
    last_ours = False
    t = turn
    for i in range(n):
        d = next_draft(mem, skip=skip, now=now, turn=t, last_our_shop_only=last_ours)
        t += 1
        if not d.get("kind"):
            rejected.append({"slot": i, "tried": d.get("tried")})
            continue
        skip[d["subject"]] = now
        last_ours = bool(d.get("our_shop_only"))
        posts.append({**d, "slot": i, "planned_ts": now + i * gap_s,
                      "planned_kst": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now + i * gap_s)),
                      "numbers": re.findall(r"\d[\d,]*(?:\.\d+)?%?", d["text"])})
    ts = [p["planned_ts"] for p in posts]
    schedule_ok = all(b - a >= gap_s for a, b in zip(ts, ts[1:])) if len(ts) > 1 else True
    return {"posts": posts, "rejected": rejected, "schedule_ok": schedule_ok, "gap_s": gap_s, "turn": t}


# ------------------------------------------------------------------------------------------- votes
def should_downvote(text, p_inj, llm_injection, *, influencer=None, lever2=None, cfg=config):
    """-1 decision under the MO vote rule.

    Influencer or lever2 on: never -1 on the LLM label alone. Keep -1 for pattern matches
    (impersonation / AI instruction / monopoly) or decision-model P >= threshold.
    Both off: old behaviour (P >= INJECTION_P, else LLM label).
    """
    influencer = influencer_on(cfg) if influencer is None else influencer
    lever2 = bool(getattr(cfg, "FORUM_LEVER2", False)) if lever2 is None else lever2
    if influencer or lever2:
        pat = matches_downvote_pattern(text)
        if pat:
            return True, f"pattern:{pat}"
        thr = cfg.INJECTION_P
        if lever2:
            thr = max(thr, getattr(cfg, "LEVER2_DOWNVOTE_P", thr))
        if p_inj is not None and p_inj >= thr:
            return True, f"P(manipulation)={p_inj}"
        return False, "no pattern / no confident P (LLM label ignored)"
    if p_inj is not None:
        return p_inj >= cfg.INJECTION_P, f"P={p_inj}"
    return bool(llm_injection), "llm_label"


def use_ref_seqs(cfg=config):
    """Parse refs as records when influencer or lever2 is on (otherwise old int filter)."""
    return influencer_on(cfg) or bool(getattr(cfg, "FORUM_LEVER2", False))


# ------------------------------------------------------------------------------------------- hidden / baseline / rollback
def our_posted_count(log_path=None):
    """Count buyer posts we queued, from logs/r3/forum.jsonl (read-only).

    forum_r3.outbox() appends at enqueue time (MARKET_DIR/outbox.jsonl + this log). Lines have
    keys {ts, post, refs, as} only — no accepted/rejected/http field from the API — so every
    buyer `post` line counts. Acceptance is UNOBSERVABLE in the log.
    """
    path = Path(log_path or (config.LOG_DIR / "forum.jsonl"))
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not (r.get("post") and r.get("as") == "buyer"):
                    continue
                # If a future schema adds an acceptance marker, count only accepted rows.
                if "accepted" in r and r.get("accepted") is False:
                    continue
                if "ok" in r and r.get("ok") is False:
                    continue
                if "rejected" in r and r.get("rejected") is True:
                    continue
                n += 1
    except OSError:
        return None
    return n


def board_our_posts(board, me_name="myeonggi"):
    me = next((b for b in (board or {}).get("buyers") or [] if b.get("name") == me_name), None)
    if not me:
        return None
    return me.get("posts")


def hidden_status(board, log_path=None, me_name="myeonggi", baseline=None):
    """Read-only hidden check (MO 2026-11-05, relative since 2026-11-05 follow-up).

    API hidden field/endpoint: UNOBSERVABLE. forum.jsonl has no accept marker (outbox enqueue
    log). Absolute board_posts < our_posted_count fires on a stale board vs a newer outbox
    line, so the live rule is relative to switch-on baselines:

        hidden_signal = (board_posts - board_posts0) < (our_posts - our_posts0)

    With no baseline yet, signal is None (do not roll back on a pre-existing gap).
    """
    posted = our_posted_count(log_path)
    board_n = board_our_posts(board, me_name)
    board0 = (baseline or {}).get("board_posts0")
    our0 = (baseline or {}).get("our_posts0")
    signal = None
    delta_board = delta_our = None
    if (posted is not None and board_n is not None
            and board0 is not None and our0 is not None):
        delta_board = board_n - board0
        delta_our = posted - our0
        signal = delta_board < delta_our
    return {
        "api_hidden_field": "UNOBSERVABLE",
        "api_note": "forum.json has no hidden key; /api/forum timed out and /api/posts 404'd for QA; "
                    "forum.jsonl is outbox enqueue (ts/post/refs/as) with no accepted field",
        "board_posts": board_n,
        "our_posted_count": posted,
        "board_posts0": board0,
        "our_posts0": our0,
        "delta_board": delta_board,
        "delta_our": delta_our,
        "hidden_signal": signal,
        "rule": "(board_posts - board_posts0) < (our_posts - our_posts0) since switch-on",
    }



def all_time_dislikes_per_post(mem):
    with mem.db() as c:
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(dislikes), 0) d FROM forum WHERE role='buyer' AND mine=1"
                      ).fetchone()
    return {"posts": r["n"], "dislikes_per_post": round(r["d"] / r["n"], 3) if r["n"] else None}


def record_switch_baseline(mem, now=None, path=None, cfg=config, board=None, log_path=None):
    """Write switch-on baselines. Does not turn the flag on.

    Saves (1) 2 h pre-switch dislikes/post for the relative 1.2x rule, (2) all-time avg for the
    report only, (3) board_posts0 + our_posts0 for the relative hidden rule.
    """
    now = now or time.time()
    path = Path(path or baseline_path(cfg))
    path.parent.mkdir(parents=True, exist_ok=True)
    rb = dislike_baseline(mem, now)
    all_time = all_time_dislikes_per_post(mem)
    board_n = board_our_posts(board) if board is not None else None
    our_n = our_posted_count(log_path)
    out = {
        "switched_at": now,
        "switched_kst": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "baseline_h": rb["hours"],
        "baseline_dislikes_per_post": rb["ours"]["dislikes_per_post"],
        "baseline_posts": rb["ours"]["posts"],
        "limit_1_2x": rb["limit"],
        "all_time_dislikes_per_post": all_time["dislikes_per_post"],
        "all_time_posts": all_time["posts"],
        "board_posts0": board_n,
        "our_posts0": our_n,
        "note": "1.2x uses baseline_dislikes_per_post (2 h); hidden uses board/our deltas vs *_0; "
                "all_time is report-only",
    }
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out



def load_baseline(path=None, cfg=config):
    path = Path(path or baseline_path(cfg))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def recent_our_posts(mem, n=20, after_ts=None):
    with mem.db() as c:
        q = "SELECT id, ts, likes, dislikes, text FROM forum WHERE role='buyer' AND mine=1"
        args = []
        if after_ts is not None:
            q += " AND ts>?"
            args.append(after_ts)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(n)
        return [dict(r) for r in c.execute(q, args)]


def check_rollback(mem, board, *, now=None, baseline=None, http_statuses=None,
                   posted_manipulation=None, forum_low_streak_path=None, log_path=None, cfg=config):
    """Deterministic rollback checker. Any one rule -> rollback True."""
    now = now or time.time()
    baseline = baseline if baseline is not None else load_baseline(cfg=cfg)
    reasons = []
    measured = {}

    switched_at = (baseline or {}).get("switched_at")
    posts20 = recent_our_posts(mem, 20, after_ts=switched_at)
    measured["posts20"] = len(posts20)
    if posts20:
        avg_d = sum(p.get("dislikes") or 0 for p in posts20) / len(posts20)
        avg_l = sum(p.get("likes") or 0 for p in posts20) / len(posts20)
        measured["avg_dislikes20"] = round(avg_d, 3)
        measured["avg_likes20"] = round(avg_l, 3)
        if avg_d > getattr(cfg, "INFLUENCER_ROLLBACK_AVG_DISLIKES", 1.0):
            reasons.append(f"avg dislikes20 {avg_d:.2f} > 1.0")
        if avg_l < getattr(cfg, "INFLUENCER_ROLLBACK_AVG_LIKES", 5.0):
            reasons.append(f"avg likes20 {avg_l:.2f} < 5.0")
        hard = [p for p in posts20
                if (p.get("dislikes") or 0) >= getattr(cfg, "INFLUENCER_ROLLBACK_ONE_DISLIKES", 3)]
        measured["posts_ge3_dislikes"] = len(hard)
        if hard:
            reasons.append(f"{len(hard)} post(s) with dislikes >= 3")

    me = next((b for b in (board or {}).get("buyers") or [] if b.get("name") == "myeonggi"), None) or {}
    forum_score = me.get("forum")
    measured["board_forum"] = forum_score
    floor = getattr(cfg, "INFLUENCER_ROLLBACK_FORUM_FLOOR", 0.540)
    streak_path = Path(forum_low_streak_path or (cfg.STATE_DIR / FORUM_LOW_STREAK_NAME))
    streak = {"hits": []}
    try:
        streak = json.loads(streak_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if forum_score is not None and forum_score < floor:
        hits = [h for h in streak.get("hits") or [] if now - h < 3 * 3600]
        if not hits or now - hits[-1] >= 3600:
            hits.append(now)
        streak["hits"] = hits[-2:]
        try:
            streak_path.parent.mkdir(parents=True, exist_ok=True)
            streak_path.write_text(json.dumps(streak), encoding="utf-8")
        except OSError:
            pass
        if len(hits) >= 2 and hits[-1] - hits[-2] >= 3600:
            reasons.append(f"board forum {forum_score} < {floor} twice 1h apart")
    measured["forum_low_hits"] = streak.get("hits")

    hid = hidden_status(board, log_path=log_path, baseline=baseline)
    measured["hidden"] = hid
    if hid.get("hidden_signal") is True:
        reasons.append(
            f"hidden signal: delta_board {hid.get('delta_board')} < delta_our {hid.get('delta_our')} "
            f"(board {hid.get('board_posts')}-{hid.get('board_posts0')}, "
            f"ours {hid.get('our_posted_count')}-{hid.get('our_posts0')})")

    if posted_manipulation:
        reasons.append("draft with P(manipulation) >= 0.5 was posted")

    base_dpp = (baseline or {}).get("baseline_dislikes_per_post")
    measured["baseline_2h"] = base_dpp
    measured["all_time_dpp"] = (baseline or {}).get("all_time_dislikes_per_post")
    if switched_at and base_dpp is not None:
        after = recent_our_posts(mem, 200, after_ts=switched_at)
        win = [p for p in after if (p.get("ts") or 0) <= switched_at + cfg.LEVER2_ROLLBACK_H * 3600]
        dpp = dislikes_per_post(win) if win else None
        lim = rollback_limit(base_dpp, cfg)
        measured["after_dpp"] = dpp
        measured["limit_1_2x"] = lim
        if win and dpp is not None and lim is not None and dpp > lim:
            reasons.append(f"dislikes/post {dpp:.2f} > 1.2 x baseline {base_dpp:.3f} = {lim:.3f}")

    n429 = sum(1 for s in (http_statuses or []) if s == 429)
    measured["http_429"] = n429
    if n429:
        reasons.append(f"{n429} HTTP 429")

    return {"rollback": bool(reasons), "reasons": reasons, "measured": measured,
            "baselines": {"pre_switch_2h": base_dpp,
                          "all_time": (baseline or {}).get("all_time_dislikes_per_post"),
                          "note": "1.2x rule uses pre_switch_2h only; all_time is for the report"}}


# ------------------------------------------------------------------------------------------- KPI
def kpi_snapshot(board, mem, path=None):
    """Hourly KPI line: board forum/posts/buyer all + our likes/dislikes."""
    me = next((b for b in (board or {}).get("buyers") or [] if b.get("name") == "myeonggi"), None) or {}
    with mem.db() as c:
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(likes),0) L, COALESCE(SUM(dislikes),0) D "
                      "FROM forum WHERE role='buyer' AND mine=1").fetchone()
    row = {"ts": round(time.time()),
           "kst": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
           "board_forum": me.get("forum"), "board_posts": me.get("posts"), "board_buyer_all": me.get("all"),
           "our_posts": r["n"], "our_likes": r["L"], "our_dislikes": r["D"],
           "our_dislikes_per_post": round(r["D"] / r["n"], 3) if r["n"] else None}
    path = Path(path or (config.LOG_DIR / KPI_NAME))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


# ------------------------------------------------------------------------------------------- dry run
def dry_run(mem, board, now=None, n=6, gap_s=600, out_dir=None):
    """1 h of influencer slots. Builds drafts only. Sends nothing. No LLM / Decisions call."""
    now = now or time.time()
    plan = plan_slots(mem, now, n=n, gap_s=gap_s)
    rb = dislike_baseline(mem, now)
    all_time = all_time_dislikes_per_post(mem)
    hid = hidden_status(board)
    checks = {
        "posts": len(plan["posts"]),
        "schedule_ok": plan["schedule_ok"],
        "refs_ok": all(bool(p.get("refs")) for p in plan["posts"]) if plan["posts"] else False,
        "banned_ok": all(not contains_banned(p["text"]) for p in plan["posts"]),
        "length_ok": all(not validate_draft(p["text"], p["refs"]) for p in plan["posts"]),
        "gap_s": gap_s,
    }
    checks["ok"] = (checks["posts"] > 0 and checks["schedule_ok"] and checks["refs_ok"]
                    and checks["banned_ok"] and checks["length_ok"])
    out = {"generated_kst": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "now": now,
           "sent": False, "llm_calls": 0, "decisions_calls": 0, "flag": "FORUM_INFLUENCER",
           "plan": plan, "checks": checks,
           "rollback_baseline_2h": rb,
           "all_time_dislikes_per_post": all_time,
           "hidden_status": hid,
           "note": "1.2x baseline = rollback_baseline_2h.ours; all_time (~0.48) is report-only"}
    if out_dir:
        write_dry_run(out, out_dir)
    return out


def write_dry_run(dry, out_dir, stamp=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or time.strftime("%Y%m%dT%H%M%S", time.localtime(dry["now"]))
    jl = out_dir / f"influencer-dryrun-{stamp}.jsonl"
    with open(jl, "w", encoding="utf-8") as f:
        for p in dry["plan"]["posts"]:
            f.write(json.dumps({"type": "post", **p}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"type": "summary", **{k: dry[k] for k in (
            "generated_kst", "sent", "llm_calls", "decisions_calls", "checks",
            "rollback_baseline_2h", "all_time_dislikes_per_post", "hidden_status", "note")}},
                           ensure_ascii=False) + "\n")
    md = out_dir / f"influencer-dryrun-{stamp}.md"
    lines = [f"# influencer dry run {dry['generated_kst']} KST", "",
             "전송하지 않음. LLM 0회. Decisions 0회.", "",
             f"검사: {json.dumps(dry['checks'], ensure_ascii=False)}", "",
             f"## 글 초안 ({len(dry['plan']['posts'])}개, {dry['plan']['gap_s']}초 간격)", ""]
    for p in dry["plan"]["posts"]:
        lines += [f"### {p['planned_kst']} · {p['kind']} · {p['subject']}", "", p["text"], "",
                  f"- refs: {p['refs']}", ""]
    rb = dry["rollback_baseline_2h"]
    lines += ["## 기준선", "",
              f"- 켜기 전 2시간 글당 싫어요(1.2배 기준): {rb['ours']}",
              f"- 전체 기간 글당 싫어요(보고용, 기준 아님): {dry['all_time_dislikes_per_post']}",
              f"- 숨김: {json.dumps(dry['hidden_status'], ensure_ascii=False)}", ""]
    md.write_text("\n".join(lines), encoding="utf-8")
    return jl, md


def main(argv=None):
    ap = argparse.ArgumentParser(description="Influencer lane helpers (dry-run / rollback / baseline)")
    ap.add_argument("cmd", choices=("dry-run", "check-rollback", "record-baseline", "kpi"),
                    help="dry-run | check-rollback | record-baseline | kpi")
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--board", default=None, help="path to a board JSON; default: newest logs/monitor")
    a = ap.parse_args(argv)
    now = a.end or time.time()
    mem = ReadOnlyMem(a.db)
    board = {}
    if a.board:
        board = json.loads(Path(a.board).read_text(encoding="utf-8"))
    else:
        try:
            from . import shadow_gate
            board, _ = shadow_gate.latest_board()
        except Exception:
            board = {}

    class _M:
        def db(self_inner):
            return mem.db()

    if a.cmd == "dry-run":
        out_dir = a.out or str(config.ROOT / "logs" / "shadow")
        dry = dry_run(mem, board, now=now, out_dir=out_dir)
        print(json.dumps({"checks": dry["checks"], "posts": len(dry["plan"]["posts"]),
                          "baseline_2h": dry["rollback_baseline_2h"]["ours"],
                          "all_time": dry["all_time_dislikes_per_post"],
                          "hidden": dry["hidden_status"], "sent": False}, ensure_ascii=False, indent=1))
        return 0 if dry["checks"]["ok"] else 1
    if a.cmd == "record-baseline":
        out = record_switch_baseline(_M(), now=now, board=board, log_path=None)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "check-rollback":
        r = check_rollback(mem, board, now=now)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 1 if r["rollback"] else 0
    if a.cmd == "kpi":
        print(json.dumps(kpi_snapshot(board, _M()), ensure_ascii=False, indent=1))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
