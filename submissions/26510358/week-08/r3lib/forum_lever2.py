"""Lever2-forum candidate (MA-3, 2026-11-05): SHADOW ONLY. Nothing live imports this module.

forum_r3.py does not import this module. The switch `config.FORUM_LEVER2` is False. forum_r3.vote() reads only
the switch itself (True: no -1 on the LLM label alone); the posting and vote plan here is not wired in until QA GO. Everything here is read-only and deterministic: SQL over
memory.sqlite (mode=ro) and fixed templates. No posting, no voting, no LLM or decision-model call.

What the candidate would do (policies/candidates/lever2-forum.json):
  1. cadence: one buyer post per LEVER2_POST_GAP_S (300 s = the kit maximum, 12/h); live posts every ~10.7 min.
  2. topics, rotating: [구매 기록] (our paid purchase: price vs list, grade and claim, use-within hours, seller,
     ledger #), [카테고리 시세] (category price/list over 3 h), [가격 동향] (category median now vs the 3 h
     before). Every post has numbers and refs that are real ledger seqs (settles).
  3. uniqueness: each post has a new subject (new category per topic or new ledger #), no repeated text and no
     near-duplicate (character 3-gram Jaccard >= LEVER2_NEAR_DUP against our earlier posts).
  4. votes: no -1 by default. -1 only when the cached P(manipulation) >= LEVER2_DOWNVOTE_P. +1 on accurate,
     useful posts (every ref is in our ledger copy and the text quotes a ref's amount or SKU). Only votes that
     count: we have >= LEVER2_VOTE_MIN_SETTLED settled deals and the author has a different GitHub owner.
  5. rollback (relative, MO 2026-11-05): baseline = our dislikes per post in the LEVER2_ROLLBACK_BASELINE_H
     before switch-on. Within LEVER2_ROLLBACK_H after switch-on, back to the old settings if our dislikes per
     post > LEVER2_ROLLBACK_RATIO x baseline, any post is hidden, or any HTTP 429.

`estimate` is an ESTIMATE of the board's buyer `posts` percentile and buyer ALL under live vs candidate cadence
(others continue at their own last-24 h post rate; surplus, deals and the vote-based forum field held fixed).
"""
import contextlib
import json
import re
import sqlite3
import statistics
import time
from pathlib import Path

from . import config, market

TOPICS = ("purchase", "category", "trend")
WINDOW_H = 3
PURCHASE_MAX_AGE_H = 6
MIN_NUMBERS = 2


def _cfg(name, default):
    return getattr(config, name, default)


def settings(cfg=config):
    """Effective forum settings. With FORUM_LEVER2 False (the default) these are exactly the live constants."""
    if not getattr(cfg, "FORUM_LEVER2", False):
        return {"lever2": False, "buyer_min_gap_s": cfg.FORUM_MIN_GAP_MIN * 60, "report_gap_s": cfg.REPORT_GAP_MIN * 60,
                "report_dedupe_h": cfg.REPORT_DEDUPE_H, "downvote_p": cfg.INJECTION_P, "upvote": "refs-in-ledger"}
    return {"lever2": True, "buyer_min_gap_s": cfg.LEVER2_POST_GAP_S, "report_gap_s": cfg.LEVER2_POST_GAP_S,
            "report_dedupe_h": cfg.REPORT_DEDUPE_H, "downvote_p": cfg.LEVER2_DOWNVOTE_P, "upvote": "accurate-useful"}


# ------------------------------------------------------------------------------------------- read-only data
class ReadOnlyMem:
    """memory.sqlite through sqlite URI mode=ro: cannot create or write the file."""

    def __init__(self, path=None):
        self.path = Path(path or config.DB_PATH).resolve()

    @contextlib.contextmanager
    def db(self):
        c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()


def won(x):
    return f"{int(x):,}"


def numbers(text):
    """Numbers quoted in a text (digits with optional thousands commas / decimals, % kept out)."""
    return [n for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "") if any(ch.isdigit() for ch in n)]


def grams(text):
    t = re.sub(r"\s+", " ", text or "").strip()
    return {t[i:i + 3] for i in range(max(0, len(t) - 2))}


def similarity(a, b):
    ga, gb = grams(a), grams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def subject_of(text):
    """Subject key of a post text in our templates: buy:<ledger#>, cat:<leaf>, trend:<leaf>, sku:<sku>."""
    t = text or ""
    m = re.search(r"ledger #(\d+)", t)
    if t.startswith("[구매 기록]") and m:
        return f"buy:{m.group(1)}"
    for tag, key in (("[카테고리 시세]", "cat"), ("[가격 동향]", "trend")):
        if t.startswith(tag):
            return f"{key}:{t[len(tag):].split(':')[0].strip()}"
    m = re.search(r"\((G[0-9a-f]{8})\)", t)
    if t.startswith("[시세]") and m:
        return f"sku:{m.group(1)}"
    return None


def check_post(draft, history, ledger_seqs, near_dup=None):
    """Problems with one draft (empty list = OK): numbers, real refs, new subject, no repeated or near-duplicate
    text. history = earlier posts [{"subject", "text"}] (ours, newest last)."""
    near_dup = near_dup if near_dup is not None else _cfg("LEVER2_NEAR_DUP", 0.8)
    bad = []
    text = draft.get("text") or ""
    if len(numbers(text)) < MIN_NUMBERS:
        bad.append("fewer than 2 numbers")
    refs = draft.get("refs") or []
    if not refs:
        bad.append("no refs")
    elif any(not isinstance(r, int) or r not in ledger_seqs for r in refs):
        bad.append("ref not a ledger seq we hold")
    subj = draft.get("subject")
    if subj and any(h.get("subject") == subj for h in history):
        bad.append(f"subject repeated: {subj}")
    norm = re.sub(r"\s+", " ", text).strip()
    if any(re.sub(r"\s+", " ", h.get("text") or "").strip() == norm for h in history):
        bad.append("text repeated")
    else:
        top = max((similarity(text, h.get("text")) for h in history), default=0.0)
        if top >= near_dup:
            bad.append(f"near-duplicate (similarity {top:.2f})")
    return bad


def check_schedule(planned_ts, gap_s=None):
    """True iff no two planned posts are closer than gap_s."""
    gap_s = gap_s if gap_s is not None else _cfg("LEVER2_POST_GAP_S", 300)
    ts = sorted(planned_ts)
    return all(b - a >= gap_s for a, b in zip(ts, ts[1:]))


# ------------------------------------------------------------------------------------------- drafts
def _cat_window(c, category, t0, t1):
    return [dict(r) for r in c.execute(
        "SELECT seq, sku, amount, list_price FROM settles WHERE category=? AND ts>? AND ts<=? AND list_price>0 "
        "AND amount>0", (category, t0, t1))]


def _q(xs):
    xs = sorted(xs)
    if len(xs) < 4:
        return xs[0], statistics.median(xs), xs[-1]
    q = statistics.quantiles(xs, n=4)
    return q[0], q[1], q[2]


def purchase_drafts(mem, now):
    """[구매 기록] for our paid purchases whose settle is in our ledger copy, newest first."""
    with mem.db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT p.*, h.name AS shop_label FROM purchases p LEFT JOIN shops h ON h.shop_url = p.shop_url "
            "WHERE p.paid=1 AND p.ledger IN (SELECT seq FROM settles) AND p.list_price>0 AND p.price>0 "
            "AND p.ts>? AND p.ts<=? ORDER BY p.ts DESC, p.ledger DESC LIMIT 60",
            (now - PURCHASE_MAX_AGE_H * 3600, now))]
    for r in rows:
        bits = [f"[구매 기록] {(r['name'] or r['sku'])[:50]} ({r['sku']})",
                f"결제 {won(r['price'])}원, 정가 {won(r['list_price'])}원의 {r['price'] / r['list_price']:.0%}"]
        if r.get("grade"):
            honest = {1: "정직", 0: "불일치"}.get(r.get("claim_honest"), "주장 없음")
            bits.append(f"등급 {r['grade']} (주장 {r.get('claim') or '-'}, {honest})")
        if r.get("use_within_hours"):
            bits.append(f"사용 기한 조건 {r['use_within_hours']:g}h")
        if r.get("warranty_hours"):
            bits.append(f"보증 {r['warranty_hours']:g}h")
        bits.append(f"판매: {r.get('shop_label') or r.get('shop_name') or r['shop_url']}")
        bits.append(f"ledger #{r['ledger']}")
        yield {"topic": "purchase", "subject": f"buy:{r['ledger']}", "text": " | ".join(bits),
               "refs": [int(r["ledger"])]}


def _busy_categories(mem, now, min_n=10):
    with mem.db() as c:
        return [r["category"] for r in c.execute(
            "SELECT category, COUNT(*) n FROM settles WHERE ts>? AND ts<=? AND category IS NOT NULL GROUP BY category "
            "HAVING n>=? ORDER BY n DESC, category LIMIT 40", (now - WINDOW_H * 3600, now, min_n))]


def category_drafts(mem, now):
    """[카테고리 시세]: price/list quartiles and the top SKU of a busy category over the last 3 h."""
    for cat in _busy_categories(mem, now):
        with mem.db() as c:
            cur = _cat_window(c, cat, now - WINDOW_H * 3600, now)
        if len(cur) < 4:
            continue
        q25, med, q75 = _q([r["amount"] / r["list_price"] for r in cur])
        counts = {}
        for r in cur:
            counts[r["sku"]] = counts.get(r["sku"], 0) + 1
        top = min(counts, key=lambda s: (-counts[s], s))
        top_rows = [r for r in cur if r["sku"] == top]
        name = (market.catalog().get(top) or {}).get("name") or top
        leaf = market.leaf(cat)
        text = (f"[카테고리 시세] {leaf}: 최근 {WINDOW_H}시간 공개 원장 정산 {len(cur)}건. 체결가/정가 중앙값 {med:.0%} "
                f"(하위 25% {q25:.0%}, 상위 25% {q75:.0%}). 최다 판매 {name[:40]} ({top}) {counts[top]}건, "
                f"체결가 중앙 {won(statistics.median(r['amount'] for r in top_rows))}원 / 정가 "
                f"{won(top_rows[0]['list_price'])}원. 근거: refs의 원장 seq.")
        refs = [r["seq"] for r in sorted(cur, key=lambda r: -r["seq"])[:5]]
        yield {"topic": "category", "subject": f"cat:{leaf}", "text": text, "refs": refs}


def trend_drafts(mem, now):
    """[가격 동향]: category median price/list in the last 3 h vs the 3 h before, with counts."""
    for cat in _busy_categories(mem, now):
        with mem.db() as c:
            cur = _cat_window(c, cat, now - WINDOW_H * 3600, now)
            prev = _cat_window(c, cat, now - 2 * WINDOW_H * 3600, now - WINDOW_H * 3600)
        if len(cur) < 4 or len(prev) < 4:
            continue
        m1 = statistics.median(r["amount"] / r["list_price"] for r in cur)
        m0 = statistics.median(r["amount"] / r["list_price"] for r in prev)
        pp = round((m1 - m0) * 100)
        word = "상승" if pp >= 2 else "하락" if pp <= -2 else "보합"
        leaf = market.leaf(cat)
        text = (f"[가격 동향] {leaf}: 체결가/정가 중앙값 {m0:.0%} → {m1:.0%} ({pp:+d}%p, {word}). "
                f"직전 {WINDOW_H}시간 {len(prev)}건, 최근 {WINDOW_H}시간 {len(cur)}건. 근거: refs의 원장 seq "
                f"(최근 3건, 직전 2건).")
        refs = [r["seq"] for r in sorted(cur, key=lambda r: -r["seq"])[:3]] + \
               [r["seq"] for r in sorted(prev, key=lambda r: -r["seq"])[:2]]
        yield {"topic": "trend", "subject": f"trend:{leaf}", "text": text, "refs": refs}


DRAFTERS = {"purchase": purchase_drafts, "category": category_drafts, "trend": trend_drafts}


def our_history(mem, now, hours=None):
    """Our buyer posts in the dedupe window as [{"subject", "text", "ts"}], oldest first."""
    hours = hours if hours is not None else config.REPORT_DEDUPE_H
    with mem.db() as c:
        rows = [dict(r) for r in c.execute("SELECT ts, text FROM forum WHERE mine=1 AND role='buyer' AND ts>? "
                                           "AND ts<=? ORDER BY ts", (now - hours * 3600, now))]
    return [{"ts": r["ts"], "text": r["text"], "subject": subject_of(r["text"])} for r in rows]


def ledger_seqs(mem, refs=None):
    with mem.db() as c:
        if refs is None:
            return {r[0] for r in c.execute("SELECT seq FROM settles")}
        refs = [int(r) for r in refs]
        out = set()
        for i in range(0, len(refs), 500):
            part = refs[i:i + 500]
            out |= {r[0] for r in c.execute(f"SELECT seq FROM settles WHERE seq IN ({','.join('?' * len(part))})",
                                            part)}
        return out


def plan_posts(mem, start, n=12, gap_s=None, history=None, topics=TOPICS):
    """The next n posts the candidate would write at start, start+gap, ... (data as of `start`). Each draft passes
    check_post against our history and the drafts before it; a topic with nothing new is skipped that slot."""
    gap_s = gap_s if gap_s is not None else _cfg("LEVER2_POST_GAP_S", 300)
    hist = list(history if history is not None else our_history(mem, start))
    gens = {t: DRAFTERS[t](mem, start) for t in topics}
    out, rejected, turn = [], [], 0
    for slot in range(n):
        planned = start + slot * gap_s
        draft = None
        for k in range(len(topics)):
            t = topics[(turn + k) % len(topics)]
            for d in gens[t]:
                bad = check_post(d, hist, ledger_seqs(mem, d["refs"]))
                if bad:
                    rejected.append({"topic": t, "subject": d["subject"], "why": bad})
                    continue
                draft = d
                break
            if draft:
                turn = (turn + k + 1) % len(topics)
                break
        if not draft:
            break
        rec = {"slot": slot, "planned_ts": planned,
               "planned_kst": time.strftime("%Y-%m-%d %H:%M", time.localtime(planned)), **draft,
               "numbers": numbers(draft["text"]), "chars": len(draft["text"])}
        out.append(rec)
        hist.append({"subject": draft["subject"], "text": draft["text"], "ts": planned})
    return {"posts": out, "rejected": rejected[:50], "rejected_n": len(rejected),
            "schedule_ok": check_schedule([p["planned_ts"] for p in out], gap_s)}


# ------------------------------------------------------------------------------------------- votes
def _ref_seqs(refs):
    out = []
    for r in refs or []:
        s = r.get("seq") if isinstance(r, dict) else r
        if isinstance(s, int):
            out.append(s)
    return out


def accurate(post, ledger):
    """Every ref is a settle we hold (and the market did not mark it missing), and the text quotes the amount or
    the SKU of at least one ref. ledger: seq -> {"amount", "sku"}."""
    refs = post.get("refs")
    raw = json.loads(refs) if isinstance(refs, str) else (refs or [])
    seqs = _ref_seqs(raw)
    if not seqs or any(isinstance(r, dict) and r.get("found") is False for r in raw):
        return False
    if any(s not in ledger for s in seqs):
        return False
    for r in raw:                                   # the amount the post claims for a ref must be the ledger's
        if isinstance(r, dict) and isinstance(r.get("data"), dict) and r["data"].get("amount") is not None:
            s = r.get("seq")
            if s in ledger and ledger[s].get("amount") is not None and r["data"]["amount"] != ledger[s]["amount"]:
                return False
    text = post.get("text") or ""
    plain = text.replace(",", "")
    return any((str(ledger[s].get("amount")) in plain) or (ledger[s].get("sku") and ledger[s]["sku"] in text)
               for s in seqs)


def useful(post):
    text = post.get("text") or ""
    return post.get("role") == "buyer" and len(text) >= 40 and len(numbers(text)) >= MIN_NUMBERS


def p_manipulation(post):
    """Cached P(manipulation) from forum.signals (written by the live classifier); None without one. No call."""
    sig = post.get("signals")
    try:
        sig = json.loads(sig) if isinstance(sig, str) else (sig or {})
    except ValueError:
        sig = {}
    p = sig.get("p_manipulation")
    return p if isinstance(p, (int, float)) else None


def plan_votes(posts, ledger, owners, my_owner, my_settled, cap=None, downvote_p=None, min_settled=None):
    """Vote actions on recent posts (newest first). owners: author -> GitHub login. Returns [{id, author, action,
    why, counts}]. action in {+1, -1, None}. We skip our own owner's posts and do not vote when it would not count."""
    cap = cap if cap is not None else _cfg("LEVER2_UPVOTES_PER_RUN", 12)
    downvote_p = downvote_p if downvote_p is not None else _cfg("LEVER2_DOWNVOTE_P", 0.9)
    min_settled = min_settled if min_settled is not None else _cfg("LEVER2_VOTE_MIN_SETTLED", 5)
    out, ups = [], 0
    for p in sorted(posts, key=lambda p: -(p.get("ts") or 0)):
        owner = owners.get(p.get("author"))
        same = p.get("mine") or p.get("author") in config.MY_NAMES or (owner is not None and owner == my_owner)
        counts = my_settled >= min_settled and not same
        rec = {"id": p.get("id"), "author": p.get("author"), "action": None, "counts": counts}
        if not counts:
            rec["why"] = "same owner" if same else f"we have < {min_settled} settled deals"
        elif p.get("voted"):
            rec["why"] = "already voted"
        else:
            pm = p_manipulation(p)
            if pm is not None and pm >= downvote_p:
                rec.update(action=-1, why=f"P(manipulation) {pm:.2f} >= {downvote_p}")
            elif ups < cap and useful(p) and accurate(p, ledger):
                rec.update(action=1, why="refs in our ledger and quoted in the text")
                ups += 1
            else:
                rec["why"] = "no clear signal" if ups < cap else "upvote cap"
        out.append(rec)
    return out


# ------------------------------------------------------------------------------------------- rollback
def dislikes_per_post(posts):
    return sum(p.get("dislikes") or 0 for p in posts) / len(posts) if posts else None


def rollback_limit(baseline, cfg=config):
    """Relative rule: dislikes per post above ratio x baseline. None without a baseline (no posts before)."""
    return None if baseline is None else round(cfg.LEVER2_ROLLBACK_RATIO * baseline, 4)


def rollback_check(own_posts, http_statuses, switched_at, now, cfg=config, baseline=None):
    """Rollback rule for the first LEVER2_ROLLBACK_H hours after switch-on. own_posts: our posts [{ts, dislikes,
    hidden}], before and after the switch. baseline: our dislikes per post in the LEVER2_ROLLBACK_BASELINE_H before
    switch-on; None = measured from own_posts. Without any post before the switch there is no baseline, and only
    hidden posts and HTTP 429 trip. Returns {"rollback": bool, "reasons": [...], "measured": {...}}."""
    win_h = cfg.LEVER2_ROLLBACK_H
    if baseline is None:
        before = [p for p in own_posts
                  if switched_at - cfg.LEVER2_ROLLBACK_BASELINE_H * 3600 <= (p.get("ts") or 0) < switched_at]
        baseline = dislikes_per_post(before)
    limit = rollback_limit(baseline, cfg)
    if now - switched_at > win_h * 3600:
        return {"rollback": False, "reasons": [], "measured": {"baseline": baseline, "limit": limit},
                "note": f"outside the first {win_h} h"}
    ps = [p for p in own_posts if switched_at <= (p.get("ts") or 0) <= now]
    dpp = dislikes_per_post(ps) or 0.0
    reasons = []
    if ps and limit is not None and dpp > limit:
        reasons.append(f"dislikes per post {dpp:.2f} > {cfg.LEVER2_ROLLBACK_RATIO} x baseline {baseline:.3f}"
                       f" = {limit:.3f}")
    hidden = sum(1 for p in ps if p.get("hidden"))
    if hidden and cfg.LEVER2_ROLLBACK_ON_HIDDEN:
        reasons.append(f"{hidden} hidden post(s)")
    n429 = sum(1 for s in http_statuses if s == 429)
    if n429 and cfg.LEVER2_ROLLBACK_ON_429:
        reasons.append(f"{n429} HTTP 429")
    return {"rollback": bool(reasons), "reasons": reasons,
            "measured": {"posts": len(ps), "dislikes_per_post": round(dpp, 3), "baseline": baseline, "limit": limit,
                         "hidden": hidden, "http_429": n429}}


def dislike_baseline(mem, now, hours=None):
    """What the rollback rule would read if we switched on at `now`: dislikes per buyer post, ours (the baseline)
    and the market's, in the LEVER2_ROLLBACK_BASELINE_H before; limit = LEVER2_ROLLBACK_RATIO x ours."""
    hours = hours if hours is not None else _cfg("LEVER2_ROLLBACK_BASELINE_H", 2)
    with mem.db() as c:
        def q(mine):
            r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(dislikes), 0) d FROM forum WHERE role='buyer' AND mine=? "
                          "AND ts>? AND ts<=?", (mine, now - hours * 3600, now)).fetchone()
            return {"posts": r["n"], "dislikes_per_post": round(r["d"] / r["n"], 3) if r["n"] else None}
        ours = q(1)
        return {"hours": hours, "ours": ours, "others": q(0), "ratio": _cfg("LEVER2_ROLLBACK_RATIO", 1.2),
                "limit": rollback_limit(ours["dislikes_per_post"], config),
                "rule": "roll back if our dislikes per post in the first LEVER2_ROLLBACK_H after switch-on > limit",
                "hidden": "not stored in memory.sqlite; the live check reads it from the forum API"}


# ------------------------------------------------------------------------------------------- estimate
def _pct(rows, k, me):
    """Mid-rank percentile among ranked buyers (None counts as 0); reproduces the board's buyer `all`."""
    v = me.get(k) or 0
    others = [r.get(k) or 0 for r in rows if r is not me]
    if not others:
        return 0.0
    return (sum(1 for x in others if x < v) + 0.5 * sum(1 for x in others if x == v)) / len(others)


BUYER_WEIGHTS = {"surplus": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}   # ROUND3_STRATEGY.md, board fields


def buyer_all(rows, me):
    return sum(w * _pct(rows, k, me) for k, w in BUYER_WEIGHTS.items())


def buyer_fit(board):
    rows = [b for b in (board or {}).get("buyers") or [] if b.get("all") is not None]
    errs = [abs(buyer_all(rows, b) - b["all"]) for b in rows]
    return {"buyers": len(rows), "mean_abs_err": round(sum(errs) / len(errs), 4) if errs else None,
            "max_abs_err": round(max(errs), 4) if errs else None}


def post_rates(mem, now, hours=24):
    """author -> buyer posts per hour over the last `hours` (forum table)."""
    with mem.db() as c:
        return {r["author"]: r["n"] / hours for r in c.execute(
            "SELECT author, COUNT(*) n FROM forum WHERE role='buyer' AND ts>? AND ts<=? GROUP BY author",
            (now - hours * 3600, now))}


def estimate(board, rates, horizon_h, live_rate, cand_rate, me_name="myeonggi", target_posts=345):
    """ESTIMATE of our buyer posts percentile and buyer ALL after horizon_h hours, live vs candidate cadence.
    Others post at their own recent rate; surplus, deals and forum (votes) are held at the board values."""
    rows = [dict(b) for b in (board or {}).get("buyers") or [] if b.get("all") is not None]
    me = next((b for b in rows if b.get("name") == me_name), None)
    if not me:
        return {"estimate": True, "error": f"{me_name} not ranked on the board"}

    def project(rate):
        proj = [dict(b, posts=(b.get("posts") or 0) + (rate if b is me else rates.get(b.get("name"), 0)) * horizon_h)
                for b in rows]
        m = proj[rows.index(me)]
        return {"posts": round(m["posts"]), "posts_pct": round(_pct(proj, "posts", m), 3),
                "buyer_all": round(buyer_all(proj, m), 4)}

    now = {"posts": me.get("posts"), "posts_pct": round(_pct(rows, "posts", me), 3),
           "buyer_all": round(buyer_all(rows, me), 4), "board_all": me.get("all"), "forum": me.get("forum")}
    live, cand = project(live_rate), project(cand_rate)
    need = max(0, target_posts - (me.get("posts") or 0))
    out = {"estimate": True, "horizon_h": horizon_h, "live_rate_per_h": round(live_rate, 2),
           "candidate_rate_per_h": round(cand_rate, 2), "now": now, "live": live, "candidate": cand,
           "delta_buyer_all": round(cand["buyer_all"] - live["buyer_all"], 4),
           "hours_to_target": {"target_posts": target_posts, "live": round(need / live_rate, 1) if live_rate else None,
                               "candidate": round(need / cand_rate, 1) if cand_rate else None},
           "fit": buyer_fit(board),
           "held_fixed": ["surplus", "deals", "forum (vote-based)", "other buyers' post rates"]}
    pl = next((p for p in (board or {}).get("players") or []
               if (p.get("buyer") or {}).get("name") == me_name), None)
    if pl and pl.get("total") is not None:
        out["players_total"] = {"now": pl["total"], "live": round(pl["total"] + live["buyer_all"] - now["buyer_all"], 4),
                                "candidate": round(pl["total"] + cand["buyer_all"] - now["buyer_all"], 4)}
    return out


# ------------------------------------------------------------------------------------------- dry run
def owners_from_board(board):
    out = {}
    for k in ("buyers", "shops"):
        for r in (board or {}).get(k) or []:
            login = (r.get("owner") or {}).get("login")
            if login:
                out[r.get("name")] = login
    return out


def dry_run(mem, board, now, n=12, vote_hours=1.0, horizon_h=24.0, spec=None):
    """1 h of candidate posts (12 at 5-min slots from `now`), the vote actions on the last vote_hours of forum
    posts, the rollback baseline and the estimate. Writes nothing; nothing is sent."""
    spec = spec or {}
    gap = int(spec.get("post_gap_s") or _cfg("LEVER2_POST_GAP_S", 300))
    plan = plan_posts(mem, now, n=n, gap_s=gap)
    owners = owners_from_board(board)
    me = next((b for b in (board or {}).get("buyers") or [] if b.get("name") == "myeonggi"), {}) or {}
    my_owner = (me.get("owner") or {}).get("login")
    with mem.db() as c:
        posts = [dict(r) for r in c.execute("SELECT id, ts, author, role, text, refs, signals, mine, voted FROM forum "
                                            "WHERE ts>? AND ts<=? AND mine=0", (now - vote_hours * 3600, now))]
        seqs = sorted({s for p in posts for s in _ref_seqs(json.loads(p["refs"] or "[]"))})
        ledger = {}
        for i in range(0, len(seqs), 500):
            part = seqs[i:i + 500]
            for r in c.execute(f"SELECT seq, amount, sku FROM settles WHERE seq IN ({','.join('?' * len(part))})", part):
                ledger[r["seq"]] = {"amount": r["amount"], "sku": r["sku"]}
        settled = c.execute("SELECT COUNT(*) FROM settles WHERE ts<=? AND buyer IN (%s)"
                            % ",".join("?" * len(config.MY_NAMES)), [now, *sorted(config.MY_NAMES)]).fetchone()[0]
    all_refs = sorted({r for p in plan["posts"] for r in p["refs"]})
    held = ledger_seqs(mem, all_refs)
    for p in plan["posts"]:
        p["refs_in_ledger"] = all(r in held for r in p["refs"])
    votes = plan_votes(posts, ledger, owners, my_owner, settled)
    rates = post_rates(mem, now)
    live_rate = rates.get("myeonggi", 0.0)
    est = estimate(board, rates, horizon_h, live_rate, 3600 / gap, target_posts=int(spec.get("posts_target") or 345))
    acts = [v["action"] for v in votes]
    return {"generated_kst": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "now": now, "sent": False,
            "llm_calls": 0, "post_gap_s": gap, "plan": plan,
            "votes": {"posts_seen": len(posts), "plus": acts.count(1), "minus": acts.count(-1),
                      "none": acts.count(None), "my_settled_deals": settled, "actions": votes},
            "rollback_baseline": dislike_baseline(mem, now), "estimate": est}


def checks(dry):
    """Gate criteria on a dry run (MO 2026-11-05): 5-min gap, unique, numbers + real refs, no default -1."""
    posts = dry["plan"]["posts"]
    seen, bad = [], []
    for p in posts:
        held = set(p["refs"]) if p.get("refs_in_ledger") else set()
        bad += [f"slot {p['slot']}: {b}" for b in check_post(p, seen, held)]
        seen.append(p)
    downs = [v for v in dry["votes"]["actions"] if v["action"] == -1 and not str(v.get("why", "")).startswith("P(")]
    out = {"posts": len(posts), "schedule_ok": dry["plan"]["schedule_ok"], "unique_ok": not bad, "problems": bad,
           "min_numbers": min((len(p["numbers"]) for p in posts), default=0),
           "refs_ok": all(p.get("refs_in_ledger") for p in posts),
           "default_downvotes": len(downs),
           "uncounted_votes": sum(1 for v in dry["votes"]["actions"] if v["action"] and not v["counts"])}
    out["ok"] = (out["schedule_ok"] and out["unique_ok"] and out["refs_ok"] and not out["default_downvotes"]
                 and not out["uncounted_votes"] and len(posts) > 0)
    return out


def render_md(dry):
    e, v, rb = dry["estimate"], dry["votes"], dry["rollback_baseline"]
    lines = [f"# lever2-forum dry run {dry['generated_kst']} KST", "",
             "전송하지 않음. LLM 호출 0회. memory.sqlite 읽기 전용. 추정치임.", "",
             f"검사: {json.dumps(dry.get('checks'), ensure_ascii=False)}", "",
             f"## 글 초안 ({len(dry['plan']['posts'])}개, {dry['post_gap_s']}초 간격)", ""]
    for p in dry["plan"]["posts"]:
        lines += [f"### {p['planned_kst']} · {p['topic']} · {p['subject']}", "", p["text"], "",
                  f"- refs: {p['refs']}", f"- 숫자: {', '.join(p['numbers'][:12])}", ""]
    lines += [f"버린 초안: {dry['plan']['rejected_n']}개 (중복 주제·중복 문장·근거 없음).", "",
              f"## 투표 계획 (최근 글 {v['posts_seen']}개)", "",
              f"+1 {v['plus']}개, -1 {v['minus']}개, 없음 {v['none']}개. 우리 정산 거래 {v['my_settled_deals']}건.", ""]
    for a in [x for x in v["actions"] if x["action"]][:40]:
        lines.append(f"- {a['action']:+d} post {a['id']} ({a['author']}): {a['why']}")
    lines += ["", "## 롤백 기준선 (지금 데이터)", "",
              f"최근 {rb['hours']}시간 dislikes/글: 우리 {rb['ours']}, 다른 buyer {rb['others']}.", "",
              f"지금 켜면 기준선 = 우리 값. 켠 뒤 2시간 dislikes/글이 {rb['ratio']} x 기준선 = {rb['limit']}보다 크면 되돌림.", "",
              "## buyer 지표 추정", "", "```", json.dumps(e, ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(lines)


def write_dry_run(dry, out_dir, stamp=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or time.strftime("%Y%m%dT%H%M%S", time.localtime(dry["now"]))
    jl = out_dir / f"forum-dryrun-{stamp}.jsonl"
    with open(jl, "w", encoding="utf-8") as f:
        for p in dry["plan"]["posts"]:
            f.write(json.dumps({"type": "post", **p}, ensure_ascii=False) + "\n")
        for a in dry["votes"]["actions"]:
            if a["action"]:
                f.write(json.dumps({"type": "vote", **a}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"type": "summary", **{k: dry[k] for k in ("generated_kst", "sent", "llm_calls",
                                                                     "post_gap_s", "rollback_baseline", "estimate")},
                            "votes": {k: dry["votes"][k] for k in ("posts_seen", "plus", "minus", "none")},
                            "checks": dry.get("checks")},
                           ensure_ascii=False) + "\n")
    md = out_dir / f"forum-dryrun-{stamp}.md"
    md.write_text(render_md(dry), encoding="utf-8")
    return jl, md


def main(argv=None):
    """Dry run only: python -m r3lib.forum_lever2 [--end TS] [--out logs/shadow]. Reads memory.sqlite (mode=ro)
    and the newest logs/monitor board file. Sends nothing, calls no model."""
    import argparse
    from . import shadow_gate
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--end", type=float, default=None, help="as-of time (epoch s); default now")
    ap.add_argument("--out", default=str(shadow_gate.REPORT_DIR))
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--candidate", default=str(Path(__file__).resolve().parent.parent
                                                / "policies/candidates/lever2-forum.json"))
    a = ap.parse_args(argv)
    spec = (json.loads(Path(a.candidate).read_text(encoding="utf-8")).get("policy") or {}).get("forum") or {}
    board, _ = shadow_gate.latest_board()
    dry = dry_run(ReadOnlyMem(a.db), board, a.end or time.time(), spec=spec)
    dry["checks"] = checks(dry)
    jl, md = write_dry_run(dry, a.out)
    print(json.dumps({"checks": dry["checks"], "votes": {k: dry["votes"][k] for k in ("posts_seen", "plus", "minus",
                      "none", "my_settled_deals")}, "rollback_baseline": dry["rollback_baseline"],
                      "files": [str(jl), str(md)]}, ensure_ascii=False, indent=1))
    return 0 if dry["checks"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
