"""Week 08 manager: the slow loop. Every hour opus reviews the last hour of the shop and sets what comes next.

    python manager.py --dir .              # runs forever, one decision an hour
    python manager.py --dir . --once       # one decision now (testing)

A parameter-level meta-harness: the code that decides (restock.py, seller.py) runs on data/policy.json, and this
loop rewrites that file from the record of how the code did. It never touches the posteriors (model.py computes
them from data); it moves what data alone cannot settle: half-life and prior weight when demand shifts, money
rules, margin, concessions, C discount, warranty by grade, the liquidation start. policy.clip keeps every value in
its range and within a quarter of the range per decision, so one bad answer cannot wreck the shop.

The hourly review (added 11-02): besides the numbers, opus gets
  - its own last six decisions, each with the hour before and the hour after it (deals, margin, misses, template
    replies), and whether the shop's code is running on them (negotiations and turns carry the policy and playbook
    versions they used);
  - health: when each process last wrote (kit, collector, seller, restock, forum), template and retry rates, and
    the code's alerts;
  - excerpts of the last hour: negotiations in full (template replies, the ones that went nowhere, the longest) and
    the requests for things not in stock;
  - its notes from the last hour;
  - its memory (data/manager_memory.md): what it has learned so far, rewritten by itself every hour, at most
    MEMORY_CHARS, each lesson with the hours it rests on;
  - the archive index (data/manager_archive.jsonl): one line per past hour (deals, margin, misses, balance, alerts,
    the problems it found, what it changed); every hour of the last two days, older hours only when something
    happened.
Before deciding it may use tools (tools.py): {"tool": [{"name": ..., "args": {...}}]} instead of the decision runs
the calls and asks again with the results, at most tools.ROUNDS rounds. lookup_hour gets the raw record of past hours
from the collector DB (settles, breaks, forum posts, balance, board) and the shop's logs (negotiations verbatim, its
own decision then); sql runs one read-only SELECT on the last 72 hours; save_tool keeps a SELECT of its own as a
named tool for the hours after (data/tools_shop.json). No tool changes anything: the policy still moves only
through policy.clip below.
It answers with number changes, playbook edits (negotiation and forum guidance as text, categories restock skips;
policy.clip_playbook refuses digits, unknown categories, too long or too many), notes for the next hour, the new memory, and a
report in Korean for the owner, who reads data/reports.md (and the same report on Telegram, notify.py) after the
fact and corrects the direction when needed.

Model: opus at medium effort; when it fails, sonnet at medium. Each decision is a
line in data/manager.jsonl (summary size, the answer, what changed after clipping, the reason, the notes).

Forum: shop_post builds the shop's status post from the policy and the ledger (no claims the shop does not keep);
forum.py sends it, an hour apart and after each policy change, between its replies to posts about the shop (the
market takes one shop post per 5 minutes). Complaints forum.py answered appear in the summary below, so a policy
change can answer them; forum.py then follows up in those threads.
"""
import argparse
import json
import os
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

import harness
import model
import notify
import policy
import race
import rules
import tools

os.environ["TZ"] = "Asia/Seoul"             # the lab server runs on UTC; reports, archive hours and lookups read KST
time.tzset()

EVERY = 3600
MODELS = (("opus", "medium"), ("sonnet", "medium"))
LOG = policy.DATA / "manager.jsonl"
REPORTS = policy.DATA / "reports.md"     # the owner's after-the-fact report, newest at the bottom
TURNS = policy.DATA / "turns.jsonl"
# a process silent this long gets an alert. restock writes once per supply window (every 20 min since 11-03), so
# 15 min raised a false "restock stopped" every hour (11-04 00:01 report)
STALE = {"kit": 15 * 60, "collector": 15 * 60, "restock": 30 * 60}


def clip(text, n):
    """Shorten a text to about n characters keeping its start and its end, where a shop puts its price: a plain
    cut made "the asking price is 53" out of 53135 and the manager reported it as a bug (11-03 23:00)."""
    text = text or ""
    if len(text) <= n:
        return text
    tail = n // 3
    return f"{text[:n - tail - 5]} [...] {text[-tail:]}"
EXCERPTS, MISSED_SHOWN = 6, 12
MEMORY = policy.DATA / "manager_memory.md"
ARCHIVE = policy.DATA / "manager_archive.jsonl"
MEMORY_CHARS = 1500
ARCHIVE_FULL_H = 48                      # every hour this recent is in the index; older ones only when flagged
LOOKUP_ROUNDS, LOOKUP_HOURS = tools.ROUNDS, 3       # 11-13: the rounds are the tools' (lookup is one of them)
WHAT = ("negotiations", "settles", "broken", "forum", "balance", "board", "decision")
UPVOTES_MAX = 5
UPVOTES_OFF = True    # owner 11-06 11:40: no likes from our shop or buyer. Patch 3.4 scores buyers by stances from other
                      # accounts, so our likes raise rival buyers' forum (we had liked shaun0927 8 times, BJEon01 and
                      # SungJinho once each, the buyers now above us)
REPORT_CHARS = 1300                      # the report arrives on Telegram every hour: short (700 before the bullets, 11-05)
# Bullets, not prose (owner, 11-05 09 h: the four-sentence reports were hard to read). Sections are a header line
# each, then "- " bullets in short noun-ending phrases (개조식), one fact a bullet, "\n" between lines.
_SPEC = ("<the owner's report in Korean, in bullets: each section is a header line ('{sections}'), then its "
         "points as lines starting with '- ' (sub-points '  - '), lines joined by \\n. Each point is one fact in a "
         "short 개조식 phrase ending in a noun or '-음/-함' (e.g. '- 체결 116/323건(36%), 07시 49%에서 하락'), not a "
         "full sentence; at most four points a section, only what matters. {market}'발견' at most two points the "
         "owner does not know yet (not in your memory, not in an earlier report), each as '관찰 → 근거 (ids, numbers) "
         "→ 확인 방법', or the single point '- 없음'. '그대로 둔 이유' only when you changed nothing; '결정 필요' what "
         "only the owner can decide (a code bug, a process down, a range that holds you back), or the single point "
         "'- 없음'. The reason for each change goes in why_ko, not here. Leave out numbers that changed nothing.>")
REPORT_SPEC = _SPEC.format(sections="상황 / 발견 / 그대로 둔 이유 / 결정 필요", market="")
SHOP_REPORT_SPEC = _SPEC.format(
    sections="상황 / 시장 / 발견 / 그대로 둔 이유 / 결정 필요",
    market="'시장' from the record's competition: our share by hour, shops rising and what they sell (grade, price "
           "over list, warranty) against ours, and where our deal rate moved (food or goods, SKUs a rising shop "
           "sells or the rest, asks at or under the market or over it); and from market_gaps the one or two "
           "categories with the most market money we do not get, saying whether stock (sales while we held the "
           "SKU) or choice (our share while held) holds it, and the SKUs that sold most while we held none. ")
# What '발견' is for (owner, 11-05 09 h): what the code cannot count and the model can read, across the record's
# texts; never a restatement of the record's numbers or general advice. One rule each in the two prompts.
FINDINGS_SHOP = """4. Findings ('발견'): read for what the code cannot count, and report only what is new:
   - why buyers left: "buyers_who_left_after_our_first_reply" (their first messages): a need we did not meet
     (a warranty length, a grade, a price they named, another shop they quoted);
   - what rising shops promise: their forum posts in forum_newest (warranty, certificates, prices) against ours;
   - demand we do not serve: requests_not_in_stock_last_hour repeating a SKU or category we never hold, and
     market_gaps.orders_no_shop_could_fill_by_category (orders no shop in the market had stock for);
   - quotes our buyer got from other shops for SKUs we sell (competition.quotes_our_buyer_got_last_hour);
   - a rule or a market behaviour that changed (the rules page, a shop or account acting in a new way).
   A finding names its evidence (negotiation, post or order ids, counts) and how to check it. When checking needs
   code, put it under 결정 필요; do not answer a finding with a policy change unless the record already proves it."""
WHY_SPEC = ('"why_ko": {{"<each number you change, or negotiation / forum{skip}>": "<in Korean, one or two sentences: '
            'what in the record made you change it (the evidence, with its numbers) and what you expect it to do>", ...}},')
UPVOTE_RULE = """Upvote: "upvote" lists the forum posts by others (from the record, by id, each with why) whose
information you actually used in this decision, e.g. a report of a shop's prices or a complaint that made you change something.
Not for agreement, politeness or posts you merely read; [] when none. Each post gets our vote once."""


def log(msg):
    print(f"{time.strftime('%m-%d %H:%M:%S')} [manager] {msg}", flush=True)


def jsonl(path, since):
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("ts", 0) >= since:
                out.append(rec)
    except OSError:
        pass
    return out


def hour_metrics(since, until):
    """What one window of the shop looked like, from the seller's and restock's own logs."""
    negs = [r for r in jsonl(model.NEGOTIATIONS, since) if r["ts"] < until]
    deals = [r for r in negs if r["outcome"] == "deal"]
    turns = [r for r in jsonl(TURNS, since) if r["ts"] < until]
    restocks = [r for r in jsonl(policy.DATA / "restock.jsonl", since) if r["ts"] < until]
    return {"opened": sum(r["outcome"] == "open" for r in negs), "deals": len(deals),
            "margin_over_cost": round(sum(d["price"] / d["cost"] - 1 for d in deals) / len(deals), 3) if deals else None,
            "price_over_list": round(sum(d["price"] / d["list_price"] for d in deals) / len(deals), 3) if deals else None,
            "missed_requests": sum(1 for r in jsonl(policy.DATA / "missed.jsonl", since) if r["ts"] < until),
            "replies": len(turns), "template_replies": sum(1 for t in turns if t.get("template")),
            # the balance when the hour's last restock ran, not now: read as the current balance it made the manager
            # report "restock plans on a stale balance" for 7 hours (11-04 01:01-07:01)
            "balance_at_last_restock": restocks[-1]["balance"] if restocks else None}


def recent_decisions(now, n=6):
    """The manager's last n decisions, each with the hour before and the hour after it."""
    out = []
    for r in jsonl(LOG, 0)[-n:]:
        t = r["ts"]
        out.append({"when": time.strftime("%m-%d %H:%M", time.localtime(t)), "changed": r.get("changed"),
                    "playbook_changed": r.get("playbook_changed"), "reason": r.get("reason"),
                    "hour_before": hour_metrics(t - 3600, t), "after": hour_metrics(t, min(t + 3600, now)),
                    "after_hours": round(min(3600, now - t) / 3600, 2)})
    return out


ALERTS = policy.DATA / "alerts.md"   # rule_watch.py and share_watch.py append here, one "- MM-DD HH:MM ..." line each


def recent_alerts(now, hours=1, limit=12):
    """The alerts.md lines of the last `hours` (KST stamps), newest last, at most `limit`."""
    try:
        lines = ALERTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    cut = time.strftime("%m-%d %H:%M", time.gmtime(now + 9 * 3600 - hours * 3600))
    out, keep = [], False
    for line in lines:
        if line.startswith("- "):
            keep = line[2:13] >= cut          # same year: "MM-DD HH:MM" sorts by time
        if keep:
            out.append(line[:400])
    return out[-limit:]


def health(md, now, p, b):
    """When each process last wrote, the reply quality of the last hour, whether the code runs on the current
    policy and playbook, and the alerts the code raises by itself."""
    files = {"kit (inventory.json)": md / "inventory.json", "collector (market.db)": model.DB,
             "seller (turns.jsonl)": TURNS, "restock (restock.jsonl)": policy.DATA / "restock.jsonl",
             "forum (forum.jsonl)": policy.DATA / "forum.jsonl"}
    def mtime(f):                         # sqlite in WAL mode writes to -wal until a checkpoint
        ts = [g.stat().st_mtime for g in (f, f.with_name(f.name + "-wal")) if g.exists()]
        return max(ts) if ts else None
    seen = {k: (round((now - mtime(f)) / 60, 1) if mtime(f) else None) for k, f in files.items()}
    turns = jsonl(TURNS, now - 3600)
    opens = [r for r in jsonl(model.NEGOTIATIONS, now - 3600) if r["outcome"] == "open"]
    alerts = [f"{k} has not written for {m} minutes" for k, m in seen.items()
              if k.split()[0] in STALE and (m is None or m * 60 > STALE[k.split()[0]])]
    # first replies are templates on purpose since 11-05 01:10 (seller.FAST_OPENING, "fast" in turns.jsonl): only the
    # others are haiku failing (01-06 h the alert counted the fast ones, 294-430 a run, all false)
    tmpl = sum(1 for t in turns if t.get("template") and not t.get("fast"))
    if turns and tmpl / len(turns) > 0.3:
        alerts.append(f"{tmpl} of {len(turns)} replies in the last hour were templates (haiku failing)")
    stale_pol = sum(1 for r in opens if r.get("policy") not in (None, p.get("version")))
    stale_pb = sum(1 for r in opens if r.get("playbook") not in (None, b.get("version")))
    return {"minutes_since_last_write": seen,
            "replies_last_hour": len(turns), "template_replies": tmpl,
            "replies_needing_a_retry": sum(1 for t in turns if (t.get("tries") or 0) > 1),
            "reasons_replies_failed": dict(Counter(str(t["failed"])[:80] for t in turns if t.get("failed"))),
            "negotiations_opened_since_the_current_policy": sum(1 for r in opens if r.get("policy") == p.get("version")),
            "negotiations_opened_on_an_older_policy": stale_pol,
            "negotiations_opened_on_an_older_playbook": stale_pb,
            "alerts": alerts}


def excerpts(now):
    """Whole negotiations of the last hour: template replies first, then the ones that went nowhere, then the
    longest; and the requests for things we did not have."""
    by_task = {}
    for t in jsonl(TURNS, now - 3600):
        by_task.setdefault(t["task"], []).append(t)
    closed = {r["task"] for r in jsonl(model.NEGOTIATIONS, now - 3600) if r["outcome"] == "deal"}

    def rank(item):
        task, ts = item
        return (-any(t.get("template") for t in ts), task in closed, -len(ts))
    talks = []
    for task, ts in sorted(by_task.items(), key=rank)[:EXCERPTS]:
        talks.append({"task": task[:8], "deal": task in closed,
                      "turns": [f"buyer: {clip(t['buyer'], 300)} | shop ({t.get('unit')}, {t['price']}"
                                f"{', template' if t.get('template') else ''}): {clip(t['shop'], 300)}" for t in ts]})
    missed = Counter(r.get("text", "")[:160] for r in jsonl(policy.DATA / "missed.jsonl", now - 3600))
    return talks, [{"request": k, "times": n} for k, n in missed.most_common(MISSED_SHOWN)]


STALE_H = 24                 # a unit unsold this long is stale stock (owner 11-04)
STALE_ALERT = 1_000_000      # stale stock at cost from which the report notes it (11-06 19 h: no owner decision asked;
                             # clearance has covered every stale goods unit since 11-04 16:50, mention_alt names the C)


def stale_stock(units, now):
    """Units unsold for STALE_H hours or more: count, cost, the categories holding most of it. clearance.py marks
    down only stock bought before the 11-03 23:55 demand fix; past STALE_ALERT the owner decides on widening it."""
    old = [u for u in units if u.get("grade") != "expired" and now - (u.get("supplied_at") or now) >= STALE_H * 3600]
    by = Counter()
    for u in old:
        by[u.get("category", "").split(">")[-1]] += u.get("cost", 0)
    return {"units": len(old), "at_cost": round(sum(u.get("cost", 0) for u in old)),
            "top_categories_by_cost": [(c, round(v)) for c, v in by.most_common(5)]}


def restock_plan(units):
    """restock's last plan beside what we actually hold: the target is what the money and the demand posterior ask
    for, not stock (11-03: a target of 138 categories read as 'held' looked like a counting bug)."""
    st = json.loads((policy.DATA / "restock_state.json").read_text()) if (policy.DATA / "restock_state.json").exists() else {}
    target = st.get("target") or {}
    have = Counter(u.get("category") for u in units if u.get("grade") != "expired")
    ts = st.get("ts")
    last = (jsonl(policy.DATA / "restock.jsonl", ts - 1) if ts else [])[-1:]
    return {"note": ("restock runs in every supply window and works each plan out from the balance at that moment; "
                     "cash_to_spend and balance_when_planned are as of plan_minutes_ago, and the gap to the balance "
                     "now is sales since then, not a stale balance"),
            "plan_minutes_ago": round((time.time() - ts) / 60, 1) if ts else None,
            "balance_when_planned": last[0].get("balance") if last else None,
            "target_categories": len(target), "target_units": sum(target.values()),
            "held_categories_now": len(have), "held_units_now": sum(have.values()),
            "target_categories_with_no_stock_now": sum(1 for c in target if not have.get(c)),
            **{k: st.get(k) for k in ("cycle_minutes", "lines_per_window", "window_minutes", "reserve",
                                      "refunds_expected", "refunds_sd", "cash_to_spend", "price_ours_factor",
                                      "price_market_ratio")}}


COMP_HOURS = 3        # competition(): the last full clock hours compared
COMP_TOP = 6          # shops shown by share
RISE_PP = 2.0         # a shop up on the hour before and this many points over its lowest earlier hour is "rising"
FALL_ALERT_PP = 3.0   # our share falling every hour and by this many points in all raises an alert
COMP_SKUS = 8         # overlapping SKUs shown per rising shop


def competition(db, now, cat):
    """Who is taking the market and where our deals went (owner, 11-05 09 h: gom31's share rose 9.7 -> 15.0 ->
    18.4% over 07-09 h on our top SKUs while the 09:01 report blamed food, 2 of 323 talks). Shares are of the
    market's settled amount by clock hour; our talks are split by kind, by whether a rising shop sells the SKU, and
    by our last ask against the market's median now for the SKU and grade (model.market_grade_prices)."""
    end = now - now % 3600                                   # KST is UTC + 9 h: the clock hours line up
    hours = [end - (COMP_HOURS - i) * 3600 for i in range(COMP_HOURS)]
    label = lambda t: time.strftime("%H", time.localtime(t))
    names = {r[0]: r[1] for r in db.execute("SELECT url, name FROM shops")}
    ours = model.our_urls(db)
    amt = [Counter() for _ in hours]
    sold = {}                                                # shop -> sku -> grade -> [(ratio, warranty)]
    for ts, d in model.entries(db, "settle", hours[0]):
        if ts >= end:
            continue
        a, s = model.amount_of(d), model.sku_of(d)
        shop = "우리" if d.get("shop") in ours else names.get(d.get("shop"), d.get("shop"))
        amt[int((ts - hours[0]) // 3600)][shop] += a or 0
        x = cat.get(s) or {}
        if a and x.get("list_price") and x.get("kind", "goods") == "goods":
            sold.setdefault(shop, {}).setdefault(s, {}).setdefault(d.get("grade"), []).append(
                (a / x["list_price"], d.get("warranty_hours")))
    share = [{k: 100 * v / (sum(c.values()) or 1) for k, v in c.items()} for c in amt]
    sh = lambda shop: [round(s.get(shop, 0.0), 1) for s in share]
    top = sorted(set().union(*share), key=lambda k: -share[-1].get(k, 0))[:COMP_TOP]
    # up on the hour before and RISE_PP over its lowest earlier hour: gom31 went 14.2 -> 10.7 -> 15.8 over 06-08 h
    rising = [k for k in set().union(*share) if k != "우리" and sh(k)[-1] > sh(k)[-2]
              and sh(k)[-1] - min(sh(k)[:-1]) >= RISE_PP]
    med = lambda v: round(sorted(v)[len(v) // 2], 3) if v else None
    mine = sold.get("우리", {})
    rivals = []
    for k in sorted(rising, key=lambda k: -share[-1].get(k, 0))[:2]:
        skus = sold.get(k, {})
        both = sorted((s for s in skus if s in mine), key=lambda s: -sum(map(len, skus[s].values())))[:COMP_SKUS]
        rivals.append({"shop": k, "share_by_hour": sh(k), "goods_skus_sold": len(skus),
                       "skus_we_also_sold": [{
                           "sku": s, "name": (cat.get(s) or {}).get("name", "")[:24],
                           "by_grade": {g: {"they_n": len(v), "they_price_over_list": med([r for r, _ in v]),
                                            "they_warranty_h": Counter(w for _, w in v).most_common(1)[0][0],
                                            "our_n": len(mine[s].get(g, [])),
                                            "our_price_over_list": med([r for r, _ in mine[s].get(g, [])])}
                                        for g, v in sorted(skus[s].items(), key=str)}} for s in both]})
    # our goods and food talks of those hours, by part
    rival_skus = {s for k in rising for s in sold.get(k, {})}
    mkt = model.market_grade_prices(db, now, cat)
    turns = {}
    for t in jsonl(TURNS, hours[0]):
        turns.setdefault(t["task"], []).append(t)
    parts = {}
    for r in jsonl(model.NEGOTIATIONS, hours[0]):
        if r["outcome"] == "open" or r["ts"] >= end:
            continue
        ts_ = turns.get(r["task"], [])
        s = r.get("sku") or next((t.get("sku") for t in ts_ if t.get("sku")), None)
        g = r.get("grade") or next((t.get("grade") for t in reversed(ts_) if t.get("grade")), "A")
        ask = r.get("price") if r["outcome"] == "deal" else r.get("last_ask")
        m, lp = (mkt.get(s) or {}).get(g), (cat.get(s) or {}).get("list_price")
        keys = [f"kind:{r.get('kind')}"]
        if r.get("kind") == "goods":
            if rising:
                keys.append("goods:rival_sku" if s in rival_skus else "goods:other_sku")
            if m and lp and ask:
                keys.append("goods:ask_at_or_under_market" if ask / lp <= m else "goods:ask_over_market")
        h = int((r["ts"] - hours[0]) // 3600)
        for k in keys:
            c = parts.setdefault(k, [[0, 0, 0] for _ in hours])[h]    # talks, deals, left after our first reply
            c[0] += 1
            c[1] += r["outcome"] == "deal"
            c[2] += r["outcome"] != "deal" and len(ts_) <= 1
    # what other shops quoted our own buyer last hour for SKUs we sell: the buyer asks several shops per order
    # (buyer.jsonl `quotes`, shop -> first ask; the grade is not recorded). Only quotes for the SKU the order closed
    # on (`quote_skus`, 11-06 13 h): a round asks shops for different SKUs of the category, and a cheaper SKU's ask
    # over this list price read as an under-cost quote (lee-sungsoo "0.406" on G24f5f979 was 1,550,000 for G8ec51935)
    q = {}
    for r in jsonl(policy.DATA / "buyer.jsonl", now - 3600):
        s, lp = r.get("sku"), r.get("list")
        if s in mine and lp and isinstance(r.get("quotes"), dict):
            for shop, v in r["quotes"].items():
                if (r.get("quote_skus") or {}).get(shop) == s and isinstance(v, (int, float)) and v > 0:
                    q.setdefault(s, {}).setdefault(shop, []).append(v / lp)
    quotes = [{"sku": s, "name": (cat.get(s) or {}).get("name", "")[:24],
               "first_ask_over_list_by_shop": {k: med(v) for k, v in sorted(by.items(), key=lambda kv: med(kv[1]))},
               "we_sold_A_at": med([r for r, _ in mine[s].get("A", [])])}
              for s, by in sorted(q.items(), key=lambda kv: -sum(map(len, kv[1].values())))[:COMP_SKUS]]
    return {"hours": [label(t) for t in hours],
            "note": "share = % of the market's settled amount in that clock hour; talks as [talks, deals, deal %, "
                    "left after our first reply]; market price = the median other shops got for the SKU and grade "
                    "over the last 2 h (24 h when thin), as a share of list",
            "our_share_by_hour": sh("우리"),
            "top_shops_last_hour": {k: sh(k) for k in top},
            "rising_shops": rivals,
            "quotes_our_buyer_got_last_hour": quotes,
            "our_talks_by_hour": {k: [[n, d, round(100 * d / n) if n else None, q] for n, d, q in v]
                                  for k, v in sorted(parts.items())}}


LEFT_SHOWN = 10        # first messages of buyers who left after our first reply, shown to the manager


def left_after_first(now):
    """The first messages of last hour's buyers who left after our first reply, one per SKU first, then the rest:
    what they asked for that the opening did not meet (owner, 11-05 09 h: 134 of 201 lost goods talks in 08 h)."""
    by_task = {}
    for t in jsonl(TURNS, now - 3600):
        by_task.setdefault(t["task"], []).append(t)
    out, seen = [], set()
    gone = [r for r in jsonl(model.NEGOTIATIONS, now - 3600)
            if r["outcome"] in ("cancel", "abandoned") and len(by_task.get(r["task"], [])) == 1]
    for first_pass in (True, False):
        for r in gone:
            t = by_task[r["task"]][0]
            if len(out) >= LEFT_SHOWN or (first_pass and t.get("sku") in seen) or r["task"][:8] in {o["task"] for o in out}:
                continue
            seen.add(t.get("sku"))
            out.append({"task": r["task"][:8], "sku": t.get("sku"), "grade": t.get("grade"), "our_ask": t.get("price"),
                        "buyer": clip(t.get("buyer") or "", 300)})
    return {"count": len(gone), "examples": out}


GAP_HOURS = 6          # market_gaps(): the window
GAP_CATS = 8           # categories shown, by market amount
GAP_SKUS = 8           # SKUs sold while we held none, by market amount


def market_gaps(est, now, cat):
    """Where the market's money is and why we do not get more of it (owner, 11-05 10 h: over 12 h our share was
    10.0% of mattresses, 19.7% of sofas and 3.5% of bedding, yet 7.6-9.8% of bedding sold while we held the SKU:
    71% of bedding sales came in hours we held none, 53 of 64 SKUs never held; competition() shows only shops).
    Per category: our share of the settled amount, the share of market sales that came while we held the SKU
    (stock) and our share of those (choice and price). The SKUs that sold most while we held none. The orders our
    own buyer saw that no shop could fill (data/buyer.jsonl why_none), demand nobody serves."""
    t0 = now - GAP_HOURS * 3600
    mine = model.our_urls(est.db)
    spans = getattr(est, "_spans", None) or est._stock_spans()
    leaf = lambda s: ((cat.get(s) or {}).get("category") or "?").split(">")[-1]
    held = lambda s, ts: any(x <= ts < y for x, y in spans.get(s, []))
    by = {}                               # category -> [amount, ours, amount in hours held, ours in hours held]
    out = Counter()                       # sku -> amount sold while we held none
    skus, held_skus = {}, {}
    for ts, d in model.entries(est.db, "settle", t0):
        if ts >= now:
            continue
        s, a = model.sku_of(d), model.amount_of(d) or 0
        c, ours, h = leaf(s), d.get("shop") in mine, held(s, ts)
        v = by.setdefault(c, [0, 0, 0, 0])
        v[0] += a; v[1] += a * ours; v[2] += a * h; v[3] += a * (ours and h)
        skus.setdefault(c, set()).add(s)
        if h or ours:
            held_skus.setdefault(c, set()).add(s)
        else:
            out[s] += a
    total = sum(v[0] for v in by.values()) or 1
    pct = lambda a, b: round(100 * a / b, 1) if b else None
    cats = [{"category": c, "market_M": round(v[0] / 1e6, 1), "market_share_of_all": pct(v[0], total),
             "our_share": pct(v[1], v[0]), "market_sales_while_we_held_the_sku": pct(v[2], v[0]),
             "our_share_while_held": pct(v[3], v[2]),
             "skus_sold": len(skus[c]), "skus_we_held": len(held_skus.get(c, ()))}
            for c, v in sorted(by.items(), key=lambda kv: -kv[1][0])[:GAP_CATS]]
    rate = {c: v[3] / v[2] for c, v in by.items() if v[2]}
    gone = [{"sku": s, "name": (cat.get(s) or {}).get("name", "")[:24], "category": leaf(s),
             "market_M_while_we_held_none": round(a / 1e6, 1),
             "ours_at_our_in_stock_share_M": round(a * rate.get(leaf(s), 0) / 1e6, 1)}
            for s, a in out.most_common(GAP_SKUS)]
    seen, none = 0, Counter()
    for r in jsonl(policy.DATA / "buyer.jsonl", t0):
        seen += 1
        if (r.get("why_none") or {}).get("shops_with_offers") == 0:
            none[r.get("category")] += 1
    return {"hours": GAP_HOURS,
            "note": "amounts are settled amounts; 'held' = we had a unit of that SKU in stock at the sale. Low "
                    "market_sales_while_we_held_the_sku = we lose on stock (restock); a low our_share_while_held = "
                    "buyers pick other shops while we have it (price, terms, or buyers who pick at random)",
            "categories": cats, "skus_sold_most_while_we_held_none": gone,
            "orders_our_buyer_saw": seen, "orders_no_shop_could_fill_by_category": dict(none.most_common(6))}


def b_warranty_by_hour(negs, now):
    """Goods B talks opened with a warranty in each of the last 6 hours, clearance and not: opened, deals, mean deal
    price over list. Since 11-07 12 h a clearance B with a warranty carries the break premium on its floor
    (seller.CLEAR_WARRANTY_PREMIUM); owner_notes asks for these counts to see whether those deals fell off."""
    opened = {r["task"]: r for r in negs if r["outcome"] == "open" and r.get("grade") == "B"
              and r.get("kind") == "goods" and r.get("warranty")}
    deals = {r["task"]: r for r in negs if r["outcome"] == "deal" and r["task"] in opened}
    out = []
    for h in range(6, 0, -1):
        t0, t1 = now - h * 3600, now - (h - 1) * 3600
        row = {"hour_from": time.strftime("%H:%M", time.localtime(t0))}
        for name, clear in (("clearance", True), ("other", False)):
            ts = [t for t, r in opened.items() if t0 <= r["ts"] < t1 and bool(r.get("clearance")) == clear]
            ds = [deals[t] for t in ts if t in deals]
            row[name] = {"opened": len(ts), "deals": len(ds),
                         "deal_price_over_list": round(sum(d["price"] / d["list_price"] for d in ds) / len(ds), 3)
                         if ds else None}
        out.append(row)
    return out


MKT_H = 3              # market_pricing(): the window
MKT_BANDS = ((0, 0.95), (0.95, 0.97), (0.97, 0.99), (0.99, 1.01), (1.01, 9))


def market_pricing(negs, our_settles, units, inv, est, now):
    """What the market-based goods prices did over the last MKT_H hours (seller.MKT_PRICING; policy mkt_floor, mkt_ask).
    Talks by where the floor stood against the market's price for the SKU and grade (`mkt` on the talk's log line):
    how many, the share paid in the ledger, the price paid over the market's, the margin over the unit's own cost.
    Then per hour: goods revenue, margin over own cost, the refunds those sales are expected to bring (break rate of
    the grade x price, sales with a warranty), and what is left; cash, goods stock at cost and how many hours of
    sales it is. Refunds arrive up to 6 h after a sale, so the hour's balance alone misreads the hour."""
    t0 = now - MKT_H * 3600
    paid = {d.get("unit"): (ts, d) for ts, d in our_settles if ts >= t0 and d.get("expires_at") is None}
    cost = {r["unit"]: r["cost"] for r in negs if r.get("unit") and r.get("cost")}
    bands = {}
    for r in negs:
        if r["outcome"] != "open" or r["ts"] < t0 or r.get("kind") != "goods" or not r.get("mkt") or not r.get("floor"):
            continue
        f = r["floor"] / r["mkt"]
        k = next(f"floor {a:.2f}-{b:.2f} x market" if b < 9 else f"floor {a:.2f}+ x market"
                 for a, b in MKT_BANDS if a <= f < b)
        x = bands.setdefault(k, {"talks": 0, "paid": 0, "_p": 0.0, "_m": 0.0, "_c": 0.0})
        x["talks"] += 1
        if r["unit"] in paid:
            amt = model.amount_of(paid[r["unit"]][1]) or 0
            x["paid"] += 1
            x["_p"] += amt
            x["_m"] += r["mkt"]
            x["_c"] += r.get("cost") or 0
    for x in bands.values():
        x["paid_share"] = round(x["paid"] / x["talks"], 3)
        x["price_over_market"] = round(x["_p"] / x["_m"], 3) if x["_m"] else None
        x["margin_over_own_cost"] = round(x["_p"] / x["_c"] - 1, 3) if x["_c"] else None
        for k in ("_p", "_m", "_c"):
            del x[k]
    rev = margin = refund = n = 0.0
    for u, (ts, d) in paid.items():
        amt = model.amount_of(d) or 0
        if (est.cat.get(model.sku_of(d)) or {}).get("kind", "goods") != "goods" or u not in cost:
            continue
        n += 1
        rev += amt
        margin += amt - cost[u]
        if d.get("warranty_hours"):
            refund += est.break_rate.get(d.get("grade"), 0.0) * amt
    goods = sum(u.get("cost", 0) for u in units if u.get("kind") == "goods")
    return {"hours": MKT_H, "talks_by_floor_over_market": dict(sorted(bands.items())),
            "goods_per_hour": {"sold": round(n / MKT_H, 1), "revenue": round(rev / MKT_H),
                               "margin_over_own_cost": round(margin / MKT_H),
                               "expected_refunds": round(refund / MKT_H),
                               "expected_net": round((margin - refund) / MKT_H)},
            "cash": inv.get("balance"), "goods_stock_at_cost": round(goods),
            "goods_stock_in_hours_of_sales": round(goods / (rev / MKT_H), 1) if rev else None}


def summary(md, now):
    """What opus sees: numbers by code, plus the newest forum posts and our reviews as text."""
    p = policy.load()
    est = model.Estimates(pol=p, now=now)
    db = est.db
    inv = json.loads((md / "inventory.json").read_text(encoding="utf-8")) if (md / "inventory.json").exists() else {}
    units = inv.get("units", [])
    hour = now - 3600
    negs = jsonl(model.NEGOTIATIONS, now - 6 * 3600)
    opened = [r for r in negs if r["outcome"] == "open"]
    deals = [r for r in negs if r["outcome"] == "deal"]
    mine = model.our_urls(db)
    our_settles = [(ts, d) for ts, d in est.sales if d.get("shop") in mine]
    reviews = [(d.get("stars"), d.get("review")) for ts, d in our_settles[-15:] if d.get("review") or d.get("stars")]
    honest = [d.get("claim_honest") for _, d in our_settles if "claim_honest" in d]
    posts = db.execute("SELECT id, ts, author, role, text FROM forum_posts ORDER BY id DESC LIMIT 30").fetchall()
    votes = {r[0]: (r[1], r[2]) for r in db.execute(
        "SELECT post, likes, dislikes FROM forum_votes v WHERE polled_at = "
        "(SELECT MAX(polled_at) FROM forum_votes WHERE post = v.post)")}
    restocks = jsonl(policy.DATA / "restock.jsonl", hour)
    missed = jsonl(policy.DATA / "missed.jsonl", hour)
    bandit = model.bandit_stats(now=now)
    hot = sorted(est.categories, key=est.rate, reverse=True)[:12]
    s = {
        "time": time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(now)),
        "hours_left_in_round": round(policy.hours_left(now), 1),
        "balance": inv.get("balance"), "start": inv.get("start"), "injected": inv.get("injected"),
        "profit_so_far": (inv.get("balance") or 0) - (inv.get("start") or 0) - (inv.get("injected") or 0),
        # the board's ALL scores book since 11-05 03:45: cash (profit_so_far) + what the unsold units cost
        "book_so_far": (inv.get("balance") or 0) - (inv.get("start") or 0) - (inv.get("injected") or 0)
                       + round(sum(u.get("cost", 0) for u in units)),
        "alerts_last_hour": recent_alerts(now),
        "stock": {"units": len(units), "food_units": sum(u.get("kind") == "food" for u in units),
                  "goods_units": sum(u.get("kind") == "goods" for u in units),
                  "at_cost": round(sum(u.get("cost", 0) for u in units)),
                  "grades": dict(Counter(u.get("grade") for u in units)),
                  "food_expiring_6h": sum(1 for u in units if u.get("expires_at") and u["expires_at"] - now < 6 * 3600),
                  f"unsold_{STALE_H}h_or_more": stale_stock(units, now)},
        "restock_plan": restock_plan(units),
        "restock_last_hour": {"runs": len(restocks), "units_bought": sum(b["qty"] for r in restocks for b in r["buy"])},
        "negotiations_6h": {"opened": len(opened), "deals": len(deals),
                            "deal_price_over_list": round(sum(d["price"] / d["list_price"] for d in deals) / len(deals), 3)
                            if deals else None,
                            "deal_margin_over_cost": round(sum(d["price"] / d["cost"] - 1 for d in deals) / len(deals), 3)
                            if deals else None,
                            "steps_at_close": dict(Counter(d.get("step") for d in deals))},
        "b_warranty_talks_by_hour": b_warranty_by_hour(negs, now),
        "market_pricing": market_pricing(negs, our_settles, units, inv, est, now),
        "price_bandit_mean_reward": {f"{k}@{a}": round(v[0] / sum(v), 3) for (k, a), v in bandit.items()},
        "requests_for_items_not_in_stock_last_hour": len(missed),
        "market": {"settles_in_window": len(est.sales), "lambda0_per_category_per_hour": round(est.lambda0, 3), "prior_weight_h": round(est.prior_weight_h, 3),
                   "our_share": round(est.share_mean, 4), "our_settles": len(our_settles),
                   "hottest_categories": [(c, round(est.rate(c), 2)) for c in hot]},
        "break_rate_by_grade": {g: round(r, 3) for g, r in est.break_rate.items()},
        "break_counts_by_grade(breaks, exposure)": est.break_counts,
        # claim_honest is None when the buyer checked out without our receipt's claim (no claim made), not a lie
        "our_claims(true/false/none_made)": f"{sum(1 for h in honest if h is True)}/"
                                            f"{sum(1 for h in honest if h is False)}/{sum(1 for h in honest if h is None)}",
        "our_settles_this_round_vs_20_needed_to_rank": [
            sum(1 for r in db.execute("SELECT data FROM ledger WHERE round >= 3 AND kind='settle'")
                if json.loads(r[0]).get("shop") in mine), 20],
        "our_recent_reviews": reviews,
        "open_complaints_about_us_on_the_forum": list((json.loads((policy.DATA / "forum_state.json").read_text())
                                                       .get("complaints") or {}).values())
                                                 if (policy.DATA / "forum_state.json").exists() else [],
        "forum_newest": [{"id": r[0], "author": r[2], "role": r[3], "text": (r[4] or "")[:300],
                          "likes_dislikes": votes.get(r[0])} for r in posts],
    }
    b = policy.load_playbook()
    talks, missed_texts = excerpts(now)
    s["last_hour"] = hour_metrics(hour, now)
    s["health"] = health(md, now, p, b)
    st = s["stock"][f"unsold_{STALE_H}h_or_more"]
    if st["at_cost"] >= STALE_ALERT:
        s["health"]["alerts"].append(
            f"{STALE_H}시간 넘게 안 팔린 재고 {st['units']}개, 원가 {st['at_cost']:,}원 "
            f"(많은 곳: {', '.join(f'{c} {v:,}' for c, v in st['top_categories_by_cost'][:3])}): "
            "clearance가 이미 산 시점과 관계없이 적용 중이고 C는 첫 답장에 대안으로 언급 중, 보관료 하루 1%만 듦. "
            "소유자 결정 불필요, 원인이 새로 보이면 발견에만 적기")
    try:
        s["competition"] = comp = competition(db, now, model.catalog(db))
        o = comp["our_share_by_hour"]
        if all(b < a for a, b in zip(o, o[1:])) and o[0] - o[-1] >= FALL_ALERT_PP:
            s["health"]["alerts"].append(
                f"시장 점유율 {' → '.join(f'{v}%' for v in o)}({'·'.join(comp['hours'])}시), "
                f"{len(o) - 1}시간 연속 하락. 오른 가게: "
                f"{', '.join(r['shop'] + ' ' + '→'.join(map(str, r['share_by_hour'])) for r in comp['rising_shops']) or '없음'}")
    except Exception as e:                    # the report goes out without it rather than not at all
        s["competition"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    try:
        s["market_gaps"] = market_gaps(est, now, model.catalog(db))
    except Exception as e:
        s["market_gaps"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    try:            # owner 11-13: the Players race (race.py), alert 5 (Book) is the shop's
        new_race, r = race.alerts("shop", now)
        s["players_race"] = {"now": r["now"], "end_at_board_paces": r["end_at_board_paces"], "book": r["book"],
                             "active_alerts": r["active_alerts"]}
        s["health"]["alerts"] += new_race
    except Exception as e:
        s["players_race"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    s["your_last_decisions_and_what_followed"] = recent_decisions(now)
    s["negotiations_last_hour"] = talks
    s["buyers_who_left_after_our_first_reply"] = left_after_first(now)
    s["requests_not_in_stock_last_hour"] = missed_texts
    prior = jsonl(LOG, 0)
    s["your_notes_from_last_hour"] = prior[-1].get("notes") if prior else None
    return p, b, s, est.categories


PROMPT = """You are the manager of a shop in an agent marketplace (Round 3, 14 days). The shop's code sells units
it bought from the market, negotiates with buyer agents (haiku writes the replies; the code sets every price and
unit), answers forum posts about the shop (opus), and restocks in the market's supply windows (a few runs an hour are
expected). restock prices every unit by the gamma-Poisson demand posterior: the k-th unit of a category is worth
P(we sell at least k before the category is refilled) x the margin minus the rest x what an unsold unit costs
(storage, food ageing), with the sale price expected from the ledger (the market's ratio per category times our
shop's factor). Units are bought best value per won first until the cash above the reserve (bankruptcy line +
warranty refunds we may still owe + refund_sd standard deviations) runs out; categories that do not make the cut
are not held. restock_plan in the record shows the plan beside what is actually held. You do not trade. Once an hour you review the last hour and set what the code runs on next. The
owner does not watch; they read your report afterwards and correct you when needed.

Score (percentiles among shops): 0.5 x book (cash = balance - 5,000,000 - bankruptcy money, plus what the unsold
units cost; since 11-05 03:45, before that cash alone) + 0.3 x revenue + 0.2 x stars. A sale under cost lowers book
by the gap; an unsold unit keeps its cost in book. Food rots (thrown away at cost); goods pay 1% of cost a day in storage and a
hidden share breaks within 6 h of the sale (a warranty refunds the full price from our balance). Buyers see only
shops that hold stock in the ordered category. Our claims are always true and certificates always shown.

Owner, 11-13: the goal is first place among students on the board's Players table (shop part + buyer part, in
"players_race" as points of 200, now and at the end at the board paces). Book is the shop's part of it: the board
counts places, not won, so passing the shop above us in Book is +1.1 points (Book 2nd, from 4th: +2.2), whatever the
margin. "book" has the shops just above and below us. A Book alert in the code's alerts (the gap to the shop above
not narrower than 6 h ago) goes in '결정 필요:' with its likely cause from the record (sales, margin, refunds, stock,
cash) and the owner's options; it is no reason by itself to move a number. The buyer's part is buyer_manager's.

Policy now, with the allowed range of each number (a change moves at most a quarter of the range):
{policy}

Goods prices (since 11-10): the market draws a unit's cost at random (an A anywhere in 0.55-0.75 of list) and buyers
pay about the same whatever it cost, so the code prices goods from the market's price for the SKU and grade (other
shops' median over the last 2 h): an A's floor is mkt_floor x that price and its ask mkt_ask x, whatever the unit
cost; B and C are only raised to those. A dear unit goes under its cost and a cheap one pays for it, so judge the
whole, never one sale. min_margin sets a floor only where the market has no price for the SKU. Buyers haggle to the
floor, so mkt_floor decides both how many talks are paid and what each pays: "market_pricing" in the record shows,
for the last hours, the talks by floor over market (paid share, price, margin over own cost), goods revenue, margin,
expected refunds and expected net per hour, cash and stock. The aim is book per hour (expected_net), not the share
of the market's sales: a lower mkt_floor sells more and earns less a sale, and the share alone is no reason to lower
it. Move mkt_floor only on at least three hours of the record that agree, one step at a time, and say in "why_ko"
which expected_net you compare. restock keeps the goods stock near five hours of our goods sales; cash rising while
the stock falls toward that is intended.

Playbook now (text and categories the code runs on; you may rewrite it):
  negotiation guidance (added to haiku's prompt when it picks a grade and writes a reply): {negotiation}
  forum guidance (added to opus's prompt when it answers a post about us): {forum}
  categories restock skips, with hours left: {skips}
Write guidance in Korean (the owner reads it; haiku and opus still answer in the buyer's or the post's language).
Guidance steers how the models speak and which of the code's options they pick (grade, tone, what to say first);
it cannot change a price, a floor or a warranty, and it must contain no digits and no words for costs, floors,
margins, percents or fractions (haiku must never learn internal numbers; such guidance is refused). At most {chars} characters each. Skip a category (at most {skip_hours} hours,
at most {skip_max} at once) only for a reason in the record, e.g. it loses money or draws complaints.

What the code does by design (guidance cannot change it; guidance against it only makes haiku's words disagree with
what the shop does): an offer above the asking price gets one request to confirm (such numbers are often for several
units), then closes at the ask; the shop concedes only when the buyer raises its offer (one step down a ladder
toward the floor), holds its price when the buyer does not, and after three numbers in a row that did not rise calls
the held price final and never moves it again; an offer at or above the current price closes; the shop never walks
away; claims are true and certificates shown. If
the code itself looks wrong (a bug, a process down), say so in "problems" and the report; the owner fixes code.
Before calling something a bug, check it against the rules page below and the record: a refusal or a number that follows from the rules (an order asking more hours than a unit has, a supply window that is closed) is not a bug. "[...]" marks where this record shortened a long text; the buyer and the shop saw it whole.

The record (code-computed; the last hour's negotiations are verbatim apart from "[...]"):
{summary}

Review, in this order:
1. Your last decisions: did the code run on them (negotiations on the current versions), and what followed?
   Undo or adjust what did not work.
2. Anything wrong: alerts, a process not writing, template replies, haiku saying something off in the excerpts,
   buyers asking for what we do not hold, complaints. When the deal rate or our share moved, find where in
   "competition": food or goods, SKUs a rising shop sells or the rest, asks at or under the market or over it,
   buyers who left after our first reply. Name a cause only from a part with enough talks to carry the change
   (11-05 09:01 blamed food for a fall when food had 2 talks of 323).
3. What to change: numbers, playbook, or nothing. Change only for a reason in the record; no change is a fine
   answer.
{findings}
Answer with one JSON object only:
{{"review": "<what your last decisions did, or that there were none>",
 "problems": ["<each thing wrong you found>"],
 "changes": {{"<number name>": <value>, ...}},
 "playbook": {{"negotiation": "<the full new text, or null to keep>", "forum": "<the full new text, or null to keep>",
              "restock_skip": {{"<category>": <hours, 0 to stop skipping>}} or null}},
 "reason": "<in Korean, two sentences: what in the record, what you changed>",
 {why_spec}
 "notes": "<what to watch next hour, for yourself>",
 "upvote": [{{"id": <post id>, "why": "<in Korean, a few words: what in it you used>"}}, ...],
 "report_ko": "{report_spec}"}}"""


LOOKUP = """

Your memory (what you have learned so far, written by you):
{memory}

Archive index (one line per past hour; older hours only when something happened):
{index}

Before deciding you may use tools: answer {{"tool": [{{"name": "<tool>", "args": {{...}}}}]}} instead of the
decision (at most {calls} calls in a round; {rounds_left} rounds left). Use a tool only when its answer bears on what
you decide or report now. A question you expect to ask again is worth saving as a tool of your own (save_tool).
{tools}
{looked}
With the decision, also give "memory": the full new text of your memory (at most {chars} characters): lessons that
should outlive this hour, each with the hours it rests on, e.g. "C warranty off -> more break complaints (11-05 14:00,
11-06 09:00)". Drop what no longer holds. Keep it the same when nothing new was learned."""


def hour_of(text, now):
    """'MM-DD HH:00' (this year, local time) -> the start of that hour; None when it does not parse."""
    try:
        t = time.strptime(f"{time.localtime(now).tm_year}-{text.strip()[:11]}", "%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return None
    return time.mktime(t) // 3600 * 3600


def archive_index(now):
    lines = []
    for r in jsonl(ARCHIVE, 0):
        if now - r["ts"] > ARCHIVE_FULL_H * 3600 and not (r.get("problems") or r.get("changed") or r.get("alerts")):
            continue
        m = r.get("metrics") or {}
        lines.append(f"{r['hour']}: deals {m.get('deals')}/{m.get('opened')} opened, margin {m.get('margin_over_cost')}, "
                     f"missed {m.get('missed_requests')}, templates {m.get('template_replies')}, balance "
                     f"{m.get('balance_at_last_restock', m.get('balance_at_end'))} (at the hour's last restock)"
                     + (f" | alerts: {'; '.join(r['alerts'])[:200]}" if r.get("alerts") else "")
                     + (f" | problems: {' / '.join(r['problems'])[:300]}" if r.get("problems") else "")
                     + (f" | changed: {', '.join(r['changed'])}" if r.get("changed") else ""))
    return "\n".join(lines) or "(empty: this is the first hour)"


def archive(now, s, answer, changed, pb_changed):
    rec = {"ts": now, "hour": time.strftime("%m-%d %H:00", time.localtime(now - 3600)), "metrics": s["last_hour"],
           "alerts": s["health"]["alerts"],
           "problems": [str(x)[:200] for x in (answer or {}).get("problems") or []][:6],
           "changed": list(changed) + [f"playbook {k}" for k in pb_changed]}
    with open(ARCHIVE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def lookup(reqs, now, db):
    """The raw record of the hours asked for (the hour starting at the time given), cut to a readable size."""
    mine = model.our_urls(db)
    out = []
    for q in (reqs or [])[:LOOKUP_HOURS]:
        t0 = hour_of(str(q.get("hour", "")), now) if isinstance(q, dict) else None
        if t0 is None or t0 > now:
            out.append({"hour": q.get("hour") if isinstance(q, dict) else q, "error": "not an hour in MM-DD HH:00"})
            continue
        what = q.get("what") if isinstance(q.get("what"), list) else list(WHAT)
        t1, what = t0 + 3600, [w for w in what if w in WHAT]
        got = {"hour": time.strftime("%m-%d %H:00", time.localtime(t0))}

        def ledger(kind):
            return [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM ledger WHERE kind=? AND ts>=? AND ts<? ORDER BY seq", (kind, t0, t1))]
        if "negotiations" in what:
            by_task = {}
            for t in jsonl(TURNS, t0):
                if t["ts"] < t1:
                    by_task.setdefault(t["task"], []).append(t)
            got["negotiations"] = [[f"buyer: {clip(t['buyer'], 200)} | shop ({t.get('unit')}, {t['price']}"
                                    f"{', template' if t.get('template') else ''}): {clip(t['shop'], 200)}" for t in ts]
                                   for ts in list(by_task.values())[:8]]
            got["negotiation_outcomes"] = dict(Counter(r["outcome"] for r in jsonl(model.NEGOTIATIONS, t0)
                                                       if r["ts"] < t1))
        if "settles" in what:
            settles = ledger("settle")
            ours = [d for d in settles if d.get("shop") in mine]
            got["our_settles"] = [{k: d.get(k) for k in ("item", "price", "stars", "review", "claim", "claim_honest",
                                                          "warranty_hours") if k in d} for d in ours[:20]]
            got["market_settles"] = len(settles)
            got["market_settles_by_shop"] = dict(Counter(str(d.get("shop"))[-30:] for d in settles).most_common(8))
        if "broken" in what:
            got["broken"] = [{k: v for k, v in d.items() if len(str(v)) < 120} for d in ledger("broken")[:20]]
        if "forum" in what:
            got["forum"] = [{"id": r[0], "author": r[1], "role": r[2], "text": (r[3] or "")[:300]} for r in db.execute(
                "SELECT id, author, role, text FROM forum_posts WHERE ts>=? AND ts<? ORDER BY id LIMIT 20", (t0, t1))]
        if "balance" in what:
            b = db.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM inventory WHERE ts>=? AND ts<?", (t0, t1)).fetchone()
            if b[2]:
                first = db.execute("SELECT balance, n_units FROM inventory WHERE ts=?", (b[0],)).fetchone()
                last = db.execute("SELECT balance, n_units FROM inventory WHERE ts=?", (b[1],)).fetchone()
                got["balance"] = {"start": first[0], "end": last[0], "units_start": first[1], "units_end": last[1]}
        if "board" in what:
            r = db.execute("SELECT raw FROM board WHERE ts>=? AND ts<? ORDER BY ts DESC LIMIT 1", (t0, t1)).fetchone()
            got["board"] = (r[0] or "")[:1500] if r else None
        if "decision" in what:
            got["your_decision"] = [{k: r.get(k) for k in ("review", "problems", "changed", "playbook_changed",
                                                            "reason", "notes")}
                                    for r in jsonl(LOG, t0) if r["ts"] < t1 + 600]
        out.append(got)
    text = json.dumps(out, ensure_ascii=False)
    return text[:12000] + (" ...(cut)" if len(text) > 12000 else "")


def ask_model(prompt):
    """opus, else sonnet; the first JSON object of the answer and which model gave it."""
    for mdl, effort in MODELS:
        t0 = time.time()
        try:
            reply = harness.call(prompt, timeout=600, model=mdl, effort=effort)
        except Exception as e:
            log(f"{mdl} failed after {time.time() - t0:.0f}s: {type(e).__name__} {str(e)[:150]}")
            continue
        m = re.search(r"\{.*\}", reply, re.S)
        try:
            return json.loads(m.group()), f"{mdl} {effort} {time.time() - t0:.0f}s"
        except (AttributeError, ValueError):
            log(f"{mdl} answer had no JSON: {reply[:200]!r}")
    return None, None


def describe(p):
    fixed = lambda k: "  [fixed by the owner for now: a change is dropped]" if k in policy.OWNER_FIXED else ""
    lines = [f"  {k} = {p[k]}  (range {lo} .. {hi}){fixed(k)}" for k, (_, lo, hi) in policy.NUMBERS.items()]
    lines += [f"  {k} = {p[k]}  (true/false)" for k in policy.FLAGS]
    return "\n".join(lines)


def decide(md, now):
    p, b, s, categories = summary(md, now)
    skips = {c: round((t - now) / 3600, 1) for c, t in (b.get("restock_skip") or {}).items() if t > now}
    prompt = PROMPT.format(policy=describe(p), negotiation=json.dumps(b["negotiation"] or "(none)"),
                           forum=json.dumps(b["forum"] or "(none)"), skips=json.dumps(skips),
                           chars=policy.GUIDE_CHARS, skip_hours=policy.SKIP_HOURS, skip_max=policy.SKIP_MAX,
                           report_spec=SHOP_REPORT_SPEC, findings=FINDINGS_SHOP, why_spec=WHY_SPEC.format(skip=" / restock_skip"), summary=json.dumps(s, ensure_ascii=False, indent=1)) + "\n\n" + UPVOTE_RULE + rules.section("shop")
    memory = MEMORY.read_text(encoding="utf-8") if MEMORY.exists() else ""
    index, looked, used, answer = archive_index(now), [], [], None
    db = model.connect()
    box = tools.Toolbox("shop", now, {"lookup_hour": (
        "The raw record of past hours (negotiations word for word, settles, broken, forum, balance, board, your "
        f"decision then; at most {LOOKUP_HOURS} hours). Use it when an hour in the index or your memory bears on the decision.",
        {"hours": [{"hour": "MM-DD HH:00", "what": list(WHAT)}]},
        lambda a: lookup(a.get("hours") if isinstance(a.get("hours"), list) else [a], now, db))})
    for rnd in range(LOOKUP_ROUNDS + 1):
        left = 0 if tools.OFF else LOOKUP_ROUNDS - rnd
        extra = LOOKUP.format(memory=memory or "(empty)", index=index, calls=tools.CALLS, tools=box.describe(),
                              rounds_left=left, chars=MEMORY_CHARS,
                              looked=("\nWhat your tools returned:\n" + "\n".join(looked) + "\n") if looked else "")
        if not left:
            extra += "\nNo tool rounds left: answer with the decision now."
        answer, who = ask_model(prompt + extra)
        if answer is None:
            break
        used.append(who)
        calls = answer.get("tool")
        if not calls and answer.get("lookup"):          # the form before 11-13
            calls = [{"name": "lookup_hour", "args": {"hours": answer["lookup"] if isinstance(answer["lookup"], list)
                                                      else [answer["lookup"]]}}]
        if not (left and calls and "review" not in answer):
            break
        got = box.run(calls)
        looked.append(got)
        log(f"{who}: tools {json.dumps(calls, ensure_ascii=False)[:300]} ({len(got)} chars)")
    used = " + ".join(used)
    if answer is None or "review" not in answer:
        log("no decision this hour; the policy stays")
        report(now, used or None, s["health"]["alerts"], {}, {}, [], "모델 호출이 실패해 이번 시간은 판단하지 "
                                                                      "못했습니다. 정책과 playbook은 그대로입니다.")
        archive(now, s, None, {}, {})
        return p, False
    for k in ("changes", "playbook"):          # a model answer of the wrong shape counts as no change
        if not isinstance(answer.get(k), dict):
            answer[k] = {}
    new = policy.clip(answer["changes"], p)
    changed = {k: (p[k], new[k]) for k in list(policy.NUMBERS) + list(policy.FLAGS) if new[k] != p[k]}
    reason = str(answer.get("reason", ""))[:600]
    if changed:
        new = policy.save(new, reason)
    nb, refused = policy.clip_playbook(answer["playbook"], b, set(categories), now)
    pb_changed = {k: (b.get(k), nb.get(k)) for k in ("negotiation", "forum", "restock_skip") if nb.get(k) != b.get(k)}
    if pb_changed:
        policy.save_playbook(nb, reason)
    mem = answer.get("memory")
    if isinstance(mem, str) and mem.strip() and mem.strip() != memory.strip():
        if len(mem) > MEMORY_CHARS:
            refused.append(f"memory: longer than {MEMORY_CHARS} characters, kept the old one")
        else:
            MEMORY.write_text(mem.strip() + "\n", encoding="utf-8")
    archive(now, s, answer, changed, pb_changed)
    liked = upvote(md, answer.get("upvote"), db, "shop")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now, "model": used, "asked": answer.get("changes"), "changed": changed,
                            "playbook_asked": answer.get("playbook"), "playbook_changed": pb_changed, "why": answer.get("why_ko"),
                            "refused": refused, "review": answer.get("review"), "problems": answer.get("problems"),
                            "reason": reason, "notes": str(answer.get("notes", ""))[:1000],
                            "lookups": len(looked), "memory": answer.get("memory"), "upvoted": liked,
                            "prompt_chars": len(prompt)}, ensure_ascii=False) + "\n")
    report(now, used, s["health"]["alerts"], changed, pb_changed, refused, str(answer.get("report_ko", ""))[:REPORT_CHARS], liked,
           answer.get("why_ko"))
    log(f"{used}: {'changed ' + json.dumps(changed, ensure_ascii=False) if changed else 'no change'}"
        f"{' | playbook ' + ', '.join(pb_changed) if pb_changed else ''}"
        f"{' | refused ' + '; '.join(refused) if refused else ''} | {reason}")
    return new, bool(changed)


def upvote(md, ids, db, role):
    """Like the posts the manager says it used: known posts by others, not liked by us before, at most UPVOTES_MAX.
    `ids` holds {"id", "why"} objects (bare ids are taken too). Returns [(id, why)] sent (the kit posts the votes from .market/outbox.jsonl; they count once we have 5 deals)."""
    if UPVOTES_OFF:
        return []
    path = policy.DATA / f"votes_{role}.json"        # one file per role: the two managers never write the same one
    try:
        done = set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        done = set()
    sent = []
    for x in (ids if isinstance(ids, list) else [])[:UPVOTES_MAX]:
        why = str(x.get("why", ""))[:80] if isinstance(x, dict) else ""
        try:
            i = int(x.get("id") if isinstance(x, dict) else x)
        except (TypeError, ValueError):
            continue
        row = db.execute("SELECT author FROM forum_posts WHERE id=?", (i,)).fetchone()
        if not row or i in done or "inextro" in str(row[0]).lower():
            continue
        with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"vote": i, "value": 1, "as": role}) + "\n")
        done.add(i)
        sent.append((i, why))
    if sent:
        path.write_text(json.dumps(sorted(done)[-2000:]), encoding="utf-8")
    return sent


def change_lines(changed, pb_changed, why):
    """What the code applied, each change with the model's reason for it (why_ko): the reason is what the owner needs."""
    why = why if isinstance(why, dict) else {}
    because = lambda k: f"\n  이유: {str(why.get(k) or why.get(f'playbook {k}') or '(설명 없음)')[:300]}"
    lines = [f"- 정책 {k}: {old} → {new}{because(k)}" for k, (old, new) in changed.items()]
    for k, (old, new) in pb_changed.items():
        if k == "restock_skip":              # timestamps mean little to a reader; the categories are the change
            new = {c: time.strftime("%m-%d %H:%M까지", time.localtime(t)) for c, t in new.items()}
            lines += [f"- 재입고 건너뛰기: {json.dumps(new, ensure_ascii=False)}{because(k)}"]
        else:                                # the old text is in the previous report; the new one is the change
            lines += [f"- playbook {k} 지침 수정{because(k)}\n  새 지침: {new or '(비움)'}"]
    return lines


def report(now, used, alerts, changed, pb_changed, refused, text, liked=(), why=None):
    """One entry of the owner's report: the model's Korean summary, then what the code actually applied and why."""
    lines = [f"## {time.strftime('%m-%d %H:%M', time.localtime(now))} ({used or '판단 없음'})", ""]
    if alerts:      # the code's checks, word for word, above whatever opus wrote (as in buyer_manager)
        lines += ["[점검 필요]"] + [f"- {a}" for a in alerts] + [""]
    lines += [text.strip() or "(보고 없음)", ""]
    lines += change_lines(changed, pb_changed, why)
    if refused:
        lines += ["- 적용 거부: " + "; ".join(refused)]
    if liked:
        lines += [f"- 좋아요 #{i}: {why}" for i, why in liked]
    if not (changed or pb_changed):
        lines += ["- 변경 없음"]
    with open(REPORTS, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")
    notify.send("[가게 manager] " + "\n".join(lines).removeprefix("## "))


def shop_post(md, p, est_db):
    """The shop's forum post: what it guarantees now, with the newest of our settles as refs to check it."""
    mine = model.our_urls(est_db)
    refs = [r[0] for r in est_db.execute(
        "SELECT seq, data FROM ledger WHERE round >= 3 AND kind='settle' ORDER BY seq DESC LIMIT 2000")
        if json.loads(r[1]).get("shop") in mine][:3]
    grades = [g for g in "ABC" if p[f"warranty_{g}"]]
    warranty = (f"a {int(p['warranty_hours'])}-hour warranty on goods of grade {'/'.join(grades)}"
                if grades and p["warranty_hours"] > 0 else "no warranty on goods for now")
    text = (f"[{model.SHOP_NAME}] Certificates always shown, grades stated truthfully (check claim_honest on our "
            f"settles), {warranty}. Food and goods across many categories, restocked every supply window. Ask for the item "
            "by SKU and send your offer as a number.")
    with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text, "refs": refs, "as": "shop"}, ensure_ascii=False) + "\n")
    log(f"forum post queued (refs {refs})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    md = Path(a.dir) / ".market"
    while True:
        now, inv = time.time(), {}
        try:
            inv = json.loads((md / "inventory.json").read_text(encoding="utf-8"))
            if inv.get("round", 0) >= 3:
                decide(md, now)
            else:
                log(f"round {inv.get('round')}: waiting for Round 3")
        except Exception as e:                   # one bad hour must not stop the loop for the rest of the round
            log(f"skipped: {type(e).__name__}: {str(e)[:200]}")
        if a.once:
            return
        # at a minute past each hour, so the archive's hour and a lookup's hour cover the same window
        time.sleep(max(60, 3600 - time.time() % 3600 + 60) if inv.get("round", 0) >= 3 else 120)


if __name__ == "__main__":
    main()
