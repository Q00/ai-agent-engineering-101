"""Week 08 buyer manager: the buyer's slow loop. Every hour opus reviews the buyer's last hours and sets what comes next.

    python buyer_manager.py --dir .              # runs forever, one decision an hour
    python buyer_manager.py --dir . --once       # one decision now (testing)

The seller has manager.py; the buyer has this one, apart, so each reviews one side. Same shape: the code that buys
(market_buyer.py, buyer_forum.py) runs on data/buyer_policy.json and data/buyer_playbook.json, and this loop rewrites
them from the record. It decides on its own and reports after the fact (data/buyer_reports.md and Telegram); the owner
reads the report and corrects the direction when needed. It never touches the price model or the break rates
(model.py computes them from the ledger), and buyer_policy.clip keeps every number inside its range.

opus sees, all computed by code: orders and deals of the last hour and the last 6 hours (how orders ended, share of
list paid, surplus, seconds, shops tried, template messages), the last orders one by one (asks and offers), the
stars we gave and false claims we met, our buyer posts and their votes, settles this round against the 20 needed,
its last decisions with the hour before and after each, its notes and its memory (data/buyer_manager_memory.md).
It answers with number changes, playbook edits (negotiation and forum guidance, no digits), notes, the new memory,
problems (a code problem goes to the owner, who fixes code) and a report in Korean.

Model: opus at medium effort; when it fails, sonnet at medium. Each decision is a line in data/buyer_manager.jsonl.
"""
import argparse
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import buyer_policy
import race
import rules
import tools
import model
import notify
import policy
from manager import REPORT_CHARS, REPORT_SPEC, UPVOTE_RULE, WHY_SPEC, ask_model, change_lines, jsonl, upvote

os.environ["TZ"] = "Asia/Seoul"
time.tzset()

LOG = policy.DATA / "buyer_manager.jsonl"
REPORTS = policy.DATA / "buyer_reports.md"
MEMORY = policy.DATA / "buyer_manager_memory.md"
ORDERS = policy.DATA / "buyer.jsonl"
PURCHASES = policy.DATA / "purchases.jsonl"
POSTS = policy.DATA / "buyer_forum.jsonl"
MEMORY_CHARS = 1500
ORDERS_SHOWN = 12
PAYMENTS = Path(".market") / "payments.jsonl"      # set from --dir in main: the kit's record of what it paid
OFFSET = 300               # five minutes after the seller's manager, so the two opus calls do not overlap


def log(msg):
    print(f"{time.strftime('%m-%d %H:%M:%S')} [buyer-manager] {msg}", flush=True)


def post_kind(text):
    """A buyer post's kind, its leading "[...]" tag ("[구매 후기]"), else "other"."""
    m = re.match(r"\s*\[([^\]]{1,20})\]", text or "")
    return m.group(1) if m else "other"


def read_avoid():
    """market_buyer's avoid list (data/buyer_avoid.json): shop -> refusals in its window."""
    try:
        return json.loads((policy.DATA / "buyer_avoid.json").read_text(encoding="utf-8")).get("refusals")
    except (OSError, ValueError):
        return None


def refusal_kind(why):
    """Why the kit did not pay an agreed deal, as a kind; the reason itself is kept whole in the examples (cut at 60
    characters it lost the hours the order asked for, and the manager called a correct refusal a bug, 11-04)."""
    w = why.lower()
    if "shelf life" in w:
        return "food lasts less than the order's use_within_hours (+0.5 h): correct refusal"
    if "grade" in w:
        return "grade not worth the price under our accept policy"
    if "certificate" in w:
        return "no certificate"
    if "not in stock" in w:
        return "the shop no longer had the unit at checkout"
    return "other: " + why[:120]


def haggle_stats(rs, deals):
    """What the first offer (open_under) did (owner 11-06: the manager tunes it from these). A deal closed at our first
    offer means the shop would likely have taken less; an order lost while some ask was under our cap means we pushed
    too hard or too slowly."""
    lost = [r for r in rs if r.get("end") != "deal" and r.get("asks") and r.get("cap")
            and min(r["asks"]) <= r["cap"]]
    return {"policy_versions": dict(Counter(r.get("policy") for r in rs)),
            "paid_over_first_ask": round(sum(r["price"] / r["asks"][0] for r in deals if r.get("asks"))
                                         / max(1, sum(1 for r in deals if r.get("asks"))), 3) if deals else None,
            "deals_at_our_first_offer": sum(1 for r in deals if r.get("offers") and r.get("asks")
                                            and r["price"] <= r["offers"][0] < r["asks"][0]),
            "lost_with_an_ask_under_cap": len(lost),
            "lost_ends": dict(Counter(str(r.get("end"))[:40] for r in lost).most_common(4)),
            "mean_offers_per_deal": round(sum(len(r.get("offers") or []) for r in deals) / len(deals), 2)
            if deals else None}


def window(since, until):
    """One window of the buyer, from its own order log."""
    every = [r for r in jsonl(ORDERS, since) if r["ts"] < until]
    rs = [r for r in every if r.get("kind")]      # orders with no kind are the old chair/bookshelf/lamp kind: no
                                                  # SKU sells for them and the board does not count them
    agreed = [r for r in rs if r.get("end") == "deal" and r.get("price") and r.get("list")]
    paid = {}
    try:
        for line in PAYMENTS.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(line)
                paid[x.get("order")] = x
            except ValueError:
                pass
    except OSError:
        pass
    deals = [r for r in agreed if (paid.get(r.get("order")) or {}).get("paid")]   # agreed and paid by the kit
    msgs = Counter()
    for r in rs:
        msgs.update(r.get("messages") or {})
    refused = [(r, str(paid[r["order"]].get("reason"))) for r in agreed
               if r.get("order") in paid and not paid[r["order"]].get("paid")]
    kinds = Counter(refusal_kind(why) for _, why in refused)
    return {"orders": len(rs), "old_kind_orders_skipped": len(every) - len(rs),
            "agreed": len(agreed), "paid_deals": len(deals),
            "kit_refused": {k: {"times": n, "examples": [
                f"{why} (order use_within_hours {r.get('use_within_hours')}, kind {r.get('kind')})"
                for r, why in refused if refusal_kind(why) == k][:2]} for k, n in kinds.most_common()},
            "ends": dict(Counter(str(r.get("end"))[:60] for r in rs).most_common(8)),
            # 11-07: how the first shop ended when we moved on to another (numbers as N); shop_ends from 11-07 11 h
            "first_shop_end_when_moved_on": dict(Counter(
                re.sub(r"\d+", "N", str(r["shop_ends"][0]))[:80] for r in rs
                if len(r.get("shop_ends") or []) > 1).most_common(8)),
            "price_over_list": round(sum(r["price"] / r["list"] for r in deals) / len(deals), 3) if deals else None,
            "surplus_sum": sum(r["list"] - r["price"] for r in deals),
            # the saving as a share of the list price; since patch 3.6 the score is the share of the shop's margin
            # kept, which needs the unit's cost (not known here): the board's Saved is the score
            "saved_rate_mean": round(sum(1 - r["price"] / r["list"] for r in deals) / len(deals), 3) if deals else None,
            "deals_under_min_saved": sum(1 for r in deals if 1 - r["price"] / r["list"] < buyer_policy.load()["min_saved"]),
            "haggle": haggle_stats(rs, deals),
            "mean_secs": round(sum(r.get("secs", 0) for r in rs) / len(rs), 1) if rs else None,
            "second_shop_tried": sum(1 for r in rs if (r.get("tried") or 0) > 1),
            "messages_by_model": msgs.get("model", 0), "messages_by_template": msgs.get("template", 0)}


def wilson(likes, dislikes, z=1.96):
    """Lower bound of the share of likes: a post's worth in the forum score (average over voted posts)."""
    n = likes + dislikes
    if n == 0:
        return None
    p = likes / n
    return (p + z * z / (2 * n) - z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5) / (1 + z * z / n)


def score_now(db, us):
    """Our buyer's row on the newest board snapshot with the percentile of each scored part among ranked buyers, and
    the best Saved rates (patch 3.4: the manager weighs min_saved against Saved and deal counts)."""
    row = db.execute("SELECT raw FROM board ORDER BY ts DESC LIMIT 1").fetchone()
    if not row:
        return None
    ranked = [b for b in json.loads(row[0]).get("buyers", []) if b.get("all") is not None]
    me = next((b for b in ranked if ((b.get("owner") or {}).get("login") or "").lower() == us.lower()), None)
    if me is None or len(ranked) < 2:
        return None
    pct = lambda k: round(sum(1 for b in ranked if (b.get(k) or 0) < (me.get(k) or 0)) / (len(ranked) - 1), 3)
    return {"ours": {k: me.get(k) for k in ("saved", "forum", "deals", "posts", "served", "all")},
            "percentile_among_buyers": {k: pct(k) for k in ("saved", "forum", "deals", "posts", "all")},
            "weights": {"saved": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1},
            "best_saved_rates": sorted((round(b.get("saved") or 0, 3) for b in ranked), reverse=True)[:5],
            "most_deals": max(b.get("deals") or 0 for b in ranked)}


def our_forum(board, us):
    """Our buyer's forum score and its percentile among ranked buyers in one /api/board snapshot."""
    ranked = [b for b in board.get("buyers", []) if b.get("all") is not None]
    me = next((b for b in ranked if ((b.get("owner") or {}).get("login") or "").lower() == us.lower()), None)
    if me is None or len(ranked) < 2:
        return None, None
    f = me.get("forum") or 0
    return f, sum(1 for b in ranked if (b.get("forum") or 0) < f) / (len(ranked) - 1)


FORUM_DROP = 0.01  # how far the forum value may fall in an hour before "값 하락" fires (patch 3.5: value, not rank)
AVG_DROP = 0.03   # how far under the forum score the last 2 h of posts must average before "평균 하락" fires


def forum_alerts(db, now, us):
    """Code checks that the forum strategy's premises broke (owner 11-04: tell me when the forum policy needs a
    change). Each alert is one Korean line with its numbers; the manager explains them, the owner decides."""
    out = []
    votes = {r[0]: (r[1] or 0, r[2] or 0) for r in db.execute(
        "SELECT post, likes, dislikes FROM forum_votes v WHERE polled_at = "
        "(SELECT MAX(polled_at) FROM forum_votes WHERE post = v.post)")}
    mine = db.execute("SELECT id, ts FROM forum_posts WHERE role='buyer' AND lower(author) = ? AND ts > ? ORDER BY ts",
                      (us.lower(), now - 6 * 3600)).fetchall()
    snaps = [json.loads(r[0]) for r in db.execute("SELECT raw FROM board WHERE ts > ? ORDER BY ts", (now - 6 * 3600,))]
    score, pct = our_forum(snaps[-1], us) if snaps else (None, None)
    # 1. a dislike: our three shapes have had none since 11-04 02:05; votes land within 5 min, so one hour's posts
    # 1, 2 and 6 judge posts one by one; patch 3.4 (11-06 06:00) rates one stance per account per author, so they
    # are off (STANCES) and the bombing share stays in the record only
    hit = [(i, votes[i][1]) for i, t in mine if t > now - 3900 and votes.get(i, (0, 0))[1] > 0]
    if hit and not STANCES:
        out.append("싫어요: 우리 글 " + ", ".join(f"#{i}({d}개)" for i, d in hit) + "이 싫어요를 받았습니다")
    # 2. new posts pull the average down: posts older than 10 min of the last 2 h, against the score now, by more
    # than AVG_DROP. Until 11-04 15 h it fired every hour: most posts land at 6 likes 0 dislikes (Wilson 0.61)
    # against a 0.635 score, which moved the score 0.637 -> 0.635 in 8 h with the percentile at 0.80-0.84; the
    # owner found the hourly alert dulled it. At 0.03 twelve 6/0 posts (0.610) stay quiet, two 5/0 of twelve
    # (0.602) or one 7/1 of twelve (0.603) still trip it.
    aged = [i for i, t in mine if now - 7200 < t < now - 600]
    worth = [w for w in (wilson(*votes[i]) for i in aged if i in votes) if w is not None]
    if not STANCES and score is not None and len(worth) >= 6 and sum(worth) / len(worth) < score - AVG_DROP:
        out.append(f"평균 하락: 최근 2시간 글 {len(worth)}편의 Wilson 하한 평균 {sum(worth) / len(worth):.3f}이 "
                   f"지금 forum 점수 {score:.3f}보다 낮아, 새 글이 평균을 깎고 있습니다")
    # 3. votes stopped: the last six posts older than 10 min have no likes at all
    last6 = [i for i, t in mine if t < now - 600][-6:]
    if len(last6) == 6 and all(votes.get(i, (0, 0))[0] == 0 for i in last6):
        out.append(f"투표 끊김: 최근 글 6편(#{last6[0]}~#{last6[-1]})이 10분이 지나도 좋아요 0입니다")
    # 4. value fell: patch 3.5 (11-06) scores the forum rating as itself (0 to 1), not as a percentile, so a rival's
    # rise no longer costs us; until then this line fired on a percentile drop (17:05: 0.83 while the value rose
    # 0.885 -> 0.891). Now: the value more than FORUM_DROP under the snapshot about an hour ago
    # (board rows are stored only when the board changed, so the newest row before the hour is its value then)
    row = db.execute("SELECT raw FROM board WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (now - 3600,)).fetchone()
    before = our_forum(json.loads(row[0]), us)[0] if row else None
    if score is not None and before is not None and before - score > FORUM_DROP:
        out.append(f"값 하락: forum 값 {score:.3f}, 1시간 전 {before:.3f}에서 {before - score:.3f} 떨어졌습니다"
                   + (f" (백분위 {pct:.2f}, 참고용)" if pct is not None else ""))
    # 6. dislike bombing (owner 11-05): from 11-05 01 h one voter disliked nearly every post in the market (buyer
    # posts with a dislike: 23 h 0/290, 03 h 269/277), every kind of ours got one, and buyer_forum's 12 h rule left
    # nothing to post after 03:43. The owner holds our posts while it lasts and wants to hear when it fades.
    storm = [] if DISLIKES_OFF else dislike_share(db, now, us, votes)
    last = max((r.get("ts", 0) for r in jsonl(POSTS, now - 6 * 3600)), default=0)
    # buyer_forum posts again once one kind's newest disliked post is FORUM_VOTES_H old: the earliest kind decides
    hit = {}
    for i, t, x in db.execute("SELECT id, ts, text FROM forum_posts WHERE role='buyer' AND lower(author) = ? AND ts > ?",
                              (us.lower(), now - (FORUM_VOTES_H + 1) * 3600)):
        if votes.get(i, (0, 0))[1] > 0 and post_kind(x) in MAIN_TAGS:
            hit[post_kind(x)] = max(hit.get(post_kind(x), 0), t)
    resume = time.strftime("%H:%M", time.localtime(min(hit.values()) + FORUM_VOTES_H * 3600)) if hit else None
    if FORUM_PAUSED:                    # no automatic resume: the owner restarts posts after the bombing ends
        resume = None
    on = storm and storm[0]["share"] is not None and storm[0]["share"] >= STORM_ON
    was = any(s["share"] is not None and s["share"] >= STORM_ON for s in storm[1:])
    # 7. (owner 11-06 10:48, STANCES) dislikes on our posts of the last DISLIKE_LOOK_H: while the bombing is on, the
    # bomber's one dislike a post is expected and weighs 1 in all, so only a post with BOMB_DISLIKES or more (another
    # account too) is reported; with no bombing any dislike is reported. Reported, not paused: a stance stays
    # whether or not we keep posting, so the owner decides.
    if STANCES and not DISLIKES_OFF:
        recent = [(i, votes[i][1]) for i, t in mine if t > now - DISLIKE_LOOK_H * 3600 and i in votes]
        state = "진행" if on else "없음"
        if storm and storm[0]["share"] is not None:
            state += f"(최근 1시간 다른 buyer 글 {storm[0]['posts']}편 중 {storm[0]['disliked']}편 싫어요)"
        need = BOMB_DISLIKES if on else 1
        bad = [(i, d) for i, d in recent if d >= need]
        if bad:
            out.append(f"싫어요 경보: 테러 {state}인데 최근 {DISLIKE_LOOK_H}시간 우리 글 중 싫어요 {need}개 이상 "
                       + ", ".join(f"#{i}({d}개)" for i, d in bad) + ". "
                       + ("테러 계정 말고 다른 계정도 싫어요를 눌렀다는 뜻입니다"
                          if on else "테러가 아닌 계정의 싫어요입니다")
                       + ". 글 정지 여부는 owner가 정합니다")
    if on and not STANCES:
        hours = next((k for k, s in enumerate(storm) if s["share"] is None or s["share"] < STORM_ON), len(storm))
        out.append(f"싫어요 테러 진행: 최근 1시간 다른 buyer 글 {storm[0]['posts']}편 중 {storm[0]['disliked']}편"
                   f"({storm[0]['share']:.0%})이 싫어요를 받았습니다, {hours}시간째. 우리 buyer 글은 "
                   + ((f"{(now - last) / 60:.0f}분째 대기(의도)" if last else "6시간 넘게 대기(의도)")
                      if now - last > 1800 else "나가고 있습니다")   # last = 0 when no post in the window (11-05: "29897525분")
                   + (", 소유자 지시로 무기한 정지(테러가 끝난 게 확인될 때까지)" if FORUM_PAUSED else "")
                   + (f", 지금 규칙의 자동 재개는 {resume} 이후" if resume else ""))
    elif not STANCES and was and storm[0]["share"] is not None and storm[0]["share"] < STORM_OFF:
        out.append(f"싫어요 테러 잦아듦: 최근 1시간 다른 buyer 글 {storm[0]['posts']}편 중 {storm[0]['disliked']}편"
                   f"({storm[0]['share']:.0%})만 싫어요. 우리 buyer 글 재개를 owner가 판단할 때입니다"
                   + (f" (지금 규칙의 자동 재개는 {resume} 이후)" if resume else ""))
    # 5. posting stopped: no buyer post for 30 min (the three shapes ran out of material, or buyer_forum is down);
    # during the bombing the pause is the owner's, line 6 says so
    if now - last > 1800 and not on and FORUM_PAUSED:
        pass                            # owner 11-06 13:25: the pause is known, no hourly "글 정지" line
    elif now - last > 1800 and not on:
        out.append("글 멈춤: 마지막 buyer 글이 " + (f"{(now - last) / 60:.0f}분 전" if last else "6시간 넘게 전") + "입니다")
    return {"alerts": out, "forum_score": score, "forum_percentile": None if pct is None else round(pct, 2),
            "market_dislike_share_by_hour_newest_first": storm}


STORM_ON, STORM_OFF = 0.30, 0.10   # share of other buyers' posts with a dislike: bombing on / faded (normal: under 2%)
FORUM_VOTES_H = 12                 # buyer_forum.VOTES_H: a kind with a disliked post waits this long (not imported: its loop)
FORUM_PAUSED = True                # buyer_forum.POSTING_PAUSED (owner 11-06 13:25 paused again; no hourly line)
# was: buyer_forum.POSTING_PAUSED (owner 11-05 08:10 paused; 11-06 10:40 resumed, patch 3.4)
# (owner 11-06 13:10) dislikes are not watched any more: the bombing account dislikes every buyer post and is known,
# and the alert asked the same question every hour. No storm share, no "싫어요 경보", no hourly bombing line.
DISLIKES_OFF = True
STANCES = True                     # patch 3.4: forum rating = one stance per account per author, not per post
BOMB_DISLIKES = 2                  # during the bombing a post of ours with this many dislikes is reported (owner 11-06)
DISLIKE_LOOK_H = 2                 # our posts this many hours back are checked for dislikes (votes land within minutes)
MAIN_TAGS = ("가격 동향", "상품 시세", "구매 후기")   # the tags of buyer_forum.MAIN, the only kinds it posts (ONLY_MAIN)


def dislike_share(db, now, us, votes, hours=12):
    """Per hour (newest first), other buyers' posts 10-70 min old in that hour and how many got a dislike. An hour
    with under 20 posts is None (too few to tell)."""
    rows = db.execute("SELECT id, ts FROM forum_posts WHERE role='buyer' AND lower(author) != ? AND ts > ?",
                      (us.lower(), now - (hours + 1) * 3600)).fetchall()
    out = []
    for h in range(hours):
        hi, lo = now - 600 - h * 3600, now - 600 - (h + 1) * 3600
        ids = [i for i, t in rows if lo < t <= hi]
        d = sum(1 for i in ids if votes.get(i, (0, 0))[1] > 0)
        out.append({"hours_ago": h, "posts": len(ids), "disliked": d,
                    "share": round(d / len(ids), 2) if len(ids) >= 20 else None})
    return out


def recent_decisions(now, n=6):
    out = []
    for r in jsonl(LOG, 0)[-n:]:
        t = r["ts"]
        out.append({"when": time.strftime("%m-%d %H:%M", time.localtime(t)), "changed": r.get("changed"),
                    "playbook_changed": r.get("playbook_changed"), "reason": r.get("reason"),
                    "hour_before": window(t - 3600, t), "after": window(t, min(t + 3600, now))})
    return out


def summary(md, now):
    p, b = buyer_policy.load(), buyer_policy.load_playbook()
    db = model.connect()
    us = model.SHOP_NAME
    settles = [json.loads(r[0]) for r in db.execute("SELECT data FROM ledger WHERE round >= 3 AND kind='settle'")]
    ours = [d for d in settles if d.get("buyer") == us]
    bought = jsonl(PURCHASES, now - 6 * 3600)
    posts = jsonl(POSTS, now - 24 * 3600)
    votes = {}
    try:
        for r in db.execute("SELECT post, likes, dislikes FROM forum_votes v WHERE polled_at = "
                            "(SELECT MAX(polled_at) FROM forum_votes WHERE post = v.post)"):
            votes[r[0]] = (r[1], r[2])
        mine = db.execute("SELECT id, text FROM forum_posts WHERE role='buyer' AND lower(author) LIKE ? "
                          "ORDER BY id DESC LIMIT 10", (f"%{us.lower()}%",)).fetchall()
        others = db.execute("SELECT id, author, role, text FROM forum_posts WHERE lower(author) NOT LIKE ? "
                            "ORDER BY id DESC LIMIT 30", (f"%{us.lower()}%",)).fetchall()
    except Exception:
        mine, others = [], []
    try:
        checks = forum_alerts(db, now, us)
    except Exception as e:
        checks = {"alerts": [f"forum 점검 실패: {type(e).__name__}: {str(e)[:120]}"]}
    try:            # owner 11-13: the Players race (race.py), alerts 1-4 are the buyer's
        new_race, standing = race.alerts("buyer", now)
        standing.pop("_end_margins", None)
        standing.pop("book", None)
    except Exception as e:
        new_race, standing = [f"순위 계산 실패: {type(e).__name__}: {str(e)[:120]}"], None
    last =[r for r in jsonl(ORDERS, now - 3600) if r.get("kind")][-ORDERS_SHOWN:]
    s = {
        "time": time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(now)),
        "hours_left_in_round": round(policy.hours_left(now), 1),
        "our_buyer_settles_this_round_vs_20_needed_to_rank": [len(ours), 20],
        "market_settles_this_round": len(settles),
        "our_buyer_score_now": score_now(db, us),
        "players_race": {"standing": standing, "new_alerts": new_race},
        "last_hour": window(now - 3600, now),
        "last_6_hours": window(now - 6 * 3600, now),
        "shops_skipped_now_for_refusing_us_at_the_first_question": read_avoid(),
        "orders_last_hour": [{k: r.get(k) for k in ("category", "kind", "use_within_hours", "budget", "shop", "list", "cap", "expect",
                                                     "sku", "quotes", "quote_skus", "asks", "offers", "end", "price", "secs", "tried")}
                             for r in last],
        "our_reviews_6h": {"stars": dict(Counter(r.get("stars") for r in bought)),
                           "false_grade_claims": sum(1 for r in bought if r.get("claim_honest") is False)},
        # counts apart from the examples: 6 examples under a "24h" key read as "6 posts in 24 h" (11-04 00:39)
        "our_buyer_posts_count": {"last_hour": sum(1 for r in posts if r.get("ts", 0) >= now - 3600),
                                  "last_24h": len(posts),
                                  "by_kind_24h": dict(Counter(post_kind(r.get("text", "")) for r in posts)),
                                  "interval": "fixed by code at about 5 minutes to the end of the round"},
        "our_buyer_posts_newest_6_examples": [{"text": r.get("text", "")[:280], "how": r.get("how")}
                                              for r in posts[-6:]],
        "our_buyer_posts_on_the_forum_with_votes(likes, dislikes)": [
            {"id": r[0], "text": (r[1] or "")[:200], "votes": votes.get(r[0])} for r in mine],
        "forum_strategy_checks": checks,
        "forum_newest_by_others":[{"id": r[0], "author": r[1], "role": r[2], "text": (r[3] or "")[:300]}
                                   for r in others],
        "your_last_decisions_and_what_followed": recent_decisions(now),
        "your_notes_from_last_hour": (jsonl(LOG, 0)[-1:] or [{}])[0].get("notes"),
    }
    return p, b, s


PROMPT = """You manage the buying side of an agent in a marketplace (Round 3, 14 days). The buyer's code gets an order
(a category, a list of SKUs, a budget, for food a use_within_hours), picks shops by expected saved rate (surplus / list
price) from a price model on the public ledger, asks the best few shops for a price at once, haggles with the one whose
ask keeps the most of the shop's margin (the code chooses every offer; haiku writes the words), and pays only up to
the cap: the price where a unit of the grade the shop named still keeps min_share of the margin (below). After a
deal it rates the shop from the market's receipt and posts its purchases on the forum as facts. You do not trade.
Once an hour you review the record and set what the code runs on next. The owner does not watch; they read your
report afterwards and correct you when needed.

Score (percentiles among buyers, except the forum rating, which counts as its value 0 to 1 since patch 3.5 of 11-06;
the rules page below has the current formula):
0.5 x Saved + 0.3 x forum rating + 0.1 x number of deals + 0.1 x number of our buyer posts (hidden ones do not count).
- Saved (patch 3.6, 11-12 17:39, applied to past deals too) = sum over deals of (list price - price paid) /
  (list price - the unit's cost to the shop), at most 1, divided by (deals + 100): the share of the shop's margin we
  kept. A deal that served nothing (goods broken, food not lasting) counts -1, or 0 when a warranty refunded it.
  With thousands of deals Saved is the mean over deals, and a deal under our current Saved lowers it. A unit's cost
  is not shown; it is drawn by grade as a share of the list price (A 0.55-0.75, B 0.40-0.60, C 0.25-0.45), so a C
  at 0.60 of the list keeps less than an A at 0.72. "saved_rate_mean" and "price_over_list" in the record are still
  shares of the list price, not the score: judge Saved by "our_buyer_score_now". The won amounts do not score.
- Forum rating: one stance per voting account per author (its likes minus dislikes on all our posts); a like stance
  weighs 1 + log2(its net likes), a dislike stance 1, only from other owners' agents with 50+ deals. More posts do
  not add dislike weight from an account that already disliked us. From 11-06 12:00 an account pays for its 4th new
  dislike stance in an hour.
"our_buyer_score_now" has each part with its percentile. The kit stops an order after 300 s.

Owner, 11-13: the goal is first place among students on the board's Players table (student_total, 2.0 at most;
"players_race" gives it in points of 200, now and at the end if every buyer keeps its board pace of the last 12 h
and the shops stay as they are; "now" has our place). The buyer's Saved is what moves us most: each student buyer we pass
in Saved is +1.1 points, and passing the leader's buyer (yms979) also takes 1.1 from them. Who passes whom by the end
is decided by Saved per deal, not by today's rise: a buyer converges to its own mean, ours is about 0.87, and the
buyers just above us are at 0.77-0.85, so we pass most of them in the last days if ours holds. "rivals_in_reach"
has each with its gap now, its gap at the end and the hour we pass it. The race is for you to watch, not a reason
to change numbers: pushing harder loses deals, and min_share is the owner's. "new_alerts" are the code's checks
(Saved per deal under 0.85 for 3 h, a pass we had lost, the end gap to the leader worse by 1+ point in 6 h, our
share of the market's deals down 20%+): put each in '결정 필요:' with its likely cause from the record (which
orders, shops, grades) and the owner's options. "active_alerts" are those still on; do not raise them again.

Owner, 11-12 18:19: goods orders are bought again (they were skipped from 11-10 22:30 until now). By the patch 3.6
score a goods A under a warranty keeps as much of the margin as food does (0.85 on our 890 deals before the skip);
B and C keep less and break more, so the grade caps take an A up to 0.754 of the list, a B only under a warranty
up to 0.593, and almost no C. Expect about two thirds of the orders to be goods again, about 39 s an order, fewer
orders and more paid deals per hour (about 34 -> 43), and many goods orders ending "ask ... above our cap" or with a
receipt refused for its grade: that is the design. Report each hour the paid goods deals by grade and warranty
and the goods that broke; an A under a warranty that keeps coming under 0.8 is a reason to tell the owner.
min_saved is held at 0.32 by the owner (a change to it is ignored).

min_share (patch 3.6, owner 11-13: held at 0.75, 0.70 until then; a change to it is ignored): the least expected share of the
shop's margin a deal must keep. It sets the cap per grade as a share of the list price (A 0.745, B 0.630, C 0.516;
a message that names no grade gets B's), no offer goes under the least a unit of that grade can have cost, and the
accept names the grades that keep it at the price, so the kit refuses a lower grade from the market's certificate.
Orders ending "ask ... above our cap" on B and C units at prices the old cap took, and receipts refused for a
claim ("we pay only for ... at this price"), are this rule at work: do not report them as a problem. min_saved now
only sets the target of the stars we give (review); leave it. Judge by the board's percentiles, not by surplus in won.

open_under (owner 11-06 11 h: set to 0.75 from 0.80 as a trial, yours to move after): the first offer as a share of
the lower of the ask and the usual price. It raises Saved per deal without dropping deals as long as shops keep
talking. Read "haggle" in each window (orders carry the policy version, so compare versions): lower paid_over_first_ask
is the gain; many deals_at_our_first_offer say shops take our opening, so go lower; a rise in
lost_with_an_ask_under_cap or in deals per hour falling against the hour before says we push too hard, so go back up.
Move open_under and raise one at a time, so the effect of each can be told apart.

Policy now, with the allowed range of each number (a change moves at most a quarter of the range):
{policy}

Playbook now (text the code runs on; you may rewrite it):
  negotiation guidance (in front of haiku's prompt when it writes our offer): {negotiation}
  forum guidance (in front of haiku's prompt when it writes our buyer post): {forum}
Guidance steers words only: it cannot change an offer, the cap or which shop we pick, and it must contain no digits
and no words for percents or fractions (such guidance is refused). At most {chars} characters each. The posts must
stay facts from receipts: never how we choose shops or haggle, never our own shop, no attacks.

What the code does by design (guidance cannot change it): offers always rise and never pass min(ask, cap); we accept
at or under the shop's usual price, when the shop meets our offer, when it stops moving under the cap, or in the last
round; a second shop is opened only when the first ends without a deal and enough time is left. If the code itself
looks wrong (a bug, orders failing for one reason), say so in "problems" and the report; the owner fixes code.
Before calling something a bug, check it against the rules page below and the record: a refusal or a number that follows from the rules (an order asking more hours than a unit has, a supply window that is closed) is not a bug. "[...]" marks where this record shortened a long text; the buyer and the shop saw it whole.

The record (code-computed):
{summary}

Your memory (lessons so far, written by you):
{memory}

Review, in this order:
1. Your last decisions: what followed? Undo or adjust what did not work.
2. Anything wrong: deals far under the orders, many orders ending the same way, template messages, posts without
   likes, too few deals for the 20 needed.
   "forum_strategy_checks" holds code checks that the forum strategy's premises broke (votes stopped, the
   forum value fell over the hour, posting stopped). When "alerts" is not empty, put each in '결정 필요:' with its likely cause
   from the record and the owner's options. Do not rewrite the forum guidance for these yourself: the owner decides
   the forum strategy. Since patch 3.4 the rating takes one stance per account per author, and one account has
   disliked nearly every buyer post since 11-05 01 h; our posts go out every 5 minutes on purpose. The owner knows
   and stopped watching dislikes (11-06 13:10): do not report dislikes, the bombing or its fee in any section.
3. What to change: numbers, playbook, or nothing. Change only for a reason in the record; no change is a fine answer.
   Mind the trade: lower offers raise Saved per deal but lose deals.
4. Findings ('발견'): read the orders and posts for what the code cannot count, and report only what is new: a
   shop acting in a new way (bait asks raised after the first quote, refusals, hidden certificates, receipts over
   the ask), a shop pricing a SKU far under the others, a category whose quotes moved, a rule or a market behaviour
   that changed. Our own shop sells too: a shop that undercuts on goods it sells is worth one point. Each finding
   names its evidence (order ids, shops, counts) and how to check it; none is a fine answer.
Answer with one JSON object only:
{{"review": "<what your last decisions did, or that there were none>",
 "problems": ["<each thing wrong you found>"],
 "changes": {{"<number name>": <value>, ...}},
 "playbook": {{"negotiation": "<the full new text, or null to keep>", "forum": "<the full new text, or null to keep>"}},
 "reason": "<two sentences: what in the record, what you changed>",
 {why_spec}
 "notes": "<what to watch next hour, for yourself>",
 "upvote": [{{"id": <post id>, "why": "<in Korean, a few words: what in it you used>"}}, ...],
 "memory": "<the full new memory, at most {mem_chars} characters, each lesson with the hours it rests on>",
 "report_ko": "{report_spec}"}}"""


TOOLS = """

Before deciding you may use tools: answer {{"tool": [{{"name": "<tool>", "args": {{...}}}}]}} instead of the
decision (at most {calls} calls in a round; {rounds_left} rounds left). Use a tool only when its answer bears on what
you decide or report now. A question you expect to ask again is worth saving as a tool of your own (save_tool).
{tools}
{looked}"""


def describe(p):
    return "\n".join(f"  {k} = {p[k]}  (range {lo} .. {hi})" for k, (_, lo, hi) in buyer_policy.NUMBERS.items())


def decide(md, now):
    p, b, s = summary(md, now)
    memory = MEMORY.read_text(encoding="utf-8") if MEMORY.exists() else ""
    prompt = PROMPT.format(policy=describe(p), negotiation=json.dumps(b["negotiation"] or "(none)"),
                           forum=json.dumps(b["forum"] or "(none)"), chars=policy.GUIDE_CHARS,
                           summary=json.dumps(s, ensure_ascii=False, indent=1), memory=memory or "(empty)",
                           mem_chars=MEMORY_CHARS, report_spec=REPORT_SPEC, why_spec=WHY_SPEC.format(skip="")) + "\n\n" + UPVOTE_RULE + rules.section("buyer")
    alerts = s["forum_strategy_checks"].get("alerts") or []
    race_alerts = s["players_race"]["new_alerts"]
    box, looked, used, answer = tools.Toolbox("buyer", now), [], [], None
    for rnd in range(tools.ROUNDS + 1):                # 11-13: tools before the decision (tools.py), as the shop's manager
        left = 0 if tools.OFF else tools.ROUNDS - rnd
        extra = "" if tools.OFF else TOOLS.format(
            calls=tools.CALLS, rounds_left=left, tools=box.describe(),
            looked=("\nWhat your tools returned:\n" + "\n".join(looked) + "\n") if looked else "")
        if not left and not tools.OFF:
            extra += "\nNo tool rounds left: answer with the decision now."
        answer, who = ask_model(prompt + extra)
        if answer is None:
            break
        used.append(who)
        if not (left and answer.get("tool") and "review" not in answer):
            break
        got = box.run(answer["tool"])
        looked.append(got)
        log(f"{who}: tools {json.dumps(answer['tool'], ensure_ascii=False)[:300]} ({len(got)} chars)")
    used = " + ".join(used) or None
    if answer is None or "review" not in answer:
        log("no decision this hour; the policy stays")
        report(now, used, {}, {}, [], "모델 호출이 실패해 이번 시간은 판단하지 못했습니다. buyer 정책과 playbook은 그대로입니다.",
               alerts=alerts, race_alerts=race_alerts)
        return
    for k in ("changes", "playbook"):
        if not isinstance(answer.get(k), dict):
            answer[k] = {}
    new = buyer_policy.clip(answer["changes"], p)
    changed = {k: (p[k], new[k]) for k in buyer_policy.NUMBERS if new[k] != p[k]}
    reason = str(answer.get("reason", ""))[:600]
    if changed:
        buyer_policy.save(new, reason)
    nb, refused = buyer_policy.clip_playbook(answer["playbook"], b)
    pb_changed = {k: (b.get(k), nb.get(k)) for k in buyer_policy.GUIDE_KEYS if nb.get(k) != b.get(k)}
    if pb_changed:
        buyer_policy.save_playbook(nb, reason)
    mem = answer.get("memory")
    if isinstance(mem, str) and mem.strip() and mem.strip() != memory.strip():
        if len(mem) > MEMORY_CHARS:
            refused.append(f"memory: longer than {MEMORY_CHARS} characters, kept the old one")
        else:
            MEMORY.write_text(mem.strip() + "\n", encoding="utf-8")
    liked = upvote(md, answer.get("upvote"), model.connect(), "buyer")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now, "model": used, "upvoted": liked, "asked": answer.get("changes"), "changed": changed,
                            "playbook_asked": answer.get("playbook"), "playbook_changed": pb_changed, "why": answer.get("why_ko"),
                            "refused": refused, "review": answer.get("review"), "problems": answer.get("problems"),
                            "reason": reason, "notes": str(answer.get("notes", ""))[:1000], "tool_rounds": len(looked),
                            "prompt_chars": len(prompt)}, ensure_ascii=False) + "\n")
    report(now, used, changed, pb_changed, refused, str(answer.get("report_ko", ""))[:REPORT_CHARS], liked,
           answer.get("why_ko"), alerts, race_alerts)
    log(f"{used}: {'changed ' + json.dumps(changed, ensure_ascii=False) if changed else 'no change'}"
        f"{' | playbook ' + ', '.join(pb_changed) if pb_changed else ''}"
        f"{' | refused ' + '; '.join(refused) if refused else ''} | {reason}")


def report(now, used, changed, pb_changed, refused, text, liked=(), why=None, alerts=(), race_alerts=()):
    lines = [f"## {time.strftime('%m-%d %H:%M', time.localtime(now))} ({used or '판단 없음'})", ""]
    if race_alerts:  # race.py, raised once when they turn on
        lines += ["[순위 점검 필요]"] + [f"- {a}" for a in race_alerts] + [""]
    if alerts:      # the code's checks, word for word, so they stand out of the hourly report whatever opus wrote
        lines += ["[forum 점검 필요]"] + [f"- {a}" for a in alerts] + [""]
    lines += [text.strip() or "(보고 없음)", ""] + change_lines(changed, pb_changed, why)
    if refused:
        lines += ["- 적용 거부: " + "; ".join(refused)]
    if liked:
        lines += [f"- 좋아요 #{i}: {why}" for i, why in liked]
    if not (changed or pb_changed):
        lines += ["- 변경 없음"]
    with open(REPORTS, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")
    notify.send("[buyer manager] " + "\n".join(lines).removeprefix("## "))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    md = Path(a.dir) / ".market"
    global PAYMENTS
    PAYMENTS = md / "payments.jsonl"
    while True:
        now, inv = time.time(), {}
        try:
            inv = json.loads((md / "inventory.json").read_text(encoding="utf-8"))
            if inv.get("round", 0) >= 3:
                decide(md, now)
            else:
                log(f"round {inv.get('round')}: waiting for Round 3")
        except Exception as e:
            log(f"skipped: {type(e).__name__}: {str(e)[:200]}")
        if a.once:
            return
        time.sleep(max(60, 3600 - time.time() % 3600 + OFFSET) if inv.get("round", 0) >= 3 else 120)


if __name__ == "__main__":
    main()
