"""Grade-claim callout drafts, approved by the person (person's decision 11/07 ~15:40, option B). Replaces the
automatic ledger-record callout of depts/cs.py (off: cs.CALLOUT_AUTO).

  condition  a shop (not mine) with >= MIN_N settles in the last 24 h whose grade claim the market marked untrue
             (ledger settle claim_honest = false), leaving out
               - settles with a broken report for the same buyer, shop and SKU after the settle (breakage)
               - food (kind food in the catalog): a food grade falls while a unit waits, so an honest claim can turn
                 false through age; the ledger does not show the unit's expiry, so food is left out altogether
  draft      facts only: the count, the ledger numbers, SKU and the actual grade per settle; no guesses, no insults,
             never the word in BANNED (checked before sending)
  approval   Telegram 승인 / 거절 buttons (depts/analyst.py proposals, kind 'callout'); 승인 -> forum_once.json, the
             buyer posts it in its next slot (depts/cs.py, refs = ledger numbers); no answer in 3 h -> expired, not
             posted (analyst.housekeeping)
  limit      one draft per shop per 24 h, whatever the answer
Runs from analyst_main.py every CHECK_EVERY_S.

Follow-up (person's decision 11/07 ~15:55, option A): 24 h after the latest draft sent for a shop (any answer), the
next 24 h of that shop's claimed settles (same counting: breakage left out, food counted when the draft counted food)
  improved  >= FU_MIN_N settles and untrue share <= FU_GOOD -> a draft saying so, with the day before for comparison
  relapsed  untrue share >= FU_BAD (>= MIN_N untrue)        -> a draft with the untrue ledger numbers and the day
            before; stored under the callout key, so it also blocks a separate callout that day and gets its own
            follow-up 24 h later
  otherwise nothing. Facts and numbers only (no sarcasm: FU_BANNED is checked too). Telegram approval, buyer post,
  one follow-up per shop per 24 h, one per draft.

Stricter rules (person 11/12 ~14:40, after #82556 needed a correction):
  1. evidence the market judged only: ledger claim_honest = false (breakage left out); a draft from our own buyer's
     judgement (item mismatch, receipt refusal: kind 'item') is refused in send()
  2. a shop needs >= MIN_TOTAL (3) claimed settles in 24 h with an untrue share >= MIN_SHARE (50%); its food settles
     count only when it has >= FOOD_MIN_N (3) of them with an untrue share >= FOOD_SHARE (80%)
  3. re-checked before it is queued (approve -> recheck): every listed ledger number read again (shop, SKU, actual
     grade, claim_honest = false, no breakage) must match the draft; one callout per shop per PER_SHOP_S (72 h)
  4. a correction (kind 'correction') takes the shop off callouts for EXCLUDE_S (7 days): callout_rules.json
     excluded {shop: until}. A wrong post gets its correction within 1 h (the person's rule; drafted by hand).
"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_N = 2
MIN_TOTAL, MIN_SHARE = 3, 0.5           # 11/12 ~14:40 (person): >= 3 claimed settles, >= 50% untrue
FOOD_MIN_N, FOOD_SHARE = 3, 0.8         # food: >= 3 food settles, >= 80% untrue, else food left out
EXCLUDE_S = 7 * 86400                   # after a correction: no callout on that shop for 7 days
RULES = os.path.join(HERE, "callout_rules.json")
WINDOW_S = 24 * 3600
PER_SHOP_S = 72 * 3600                  # 11/12 ~14:40 (person): one callout per shop per 72 h (was 24 h)
EXPIRE_S = 3 * 3600
CHECK_EVERY_S = 300
MAX_LISTED = 8
# off (None) by default. A share like 0.5: a shop whose untrue share of its 24 h claimed settles is at least this has
# its food settles counted too (aging makes a few food claims untrue; it cannot make most of them untrue)
FOOD_SHARE_MIN = 0.5             # person's decision 11/07 ~15:45: on (elm-street 16 of 17 untrue)
BANNED = ("사기", "사기꾼", "도둑", "scam", "fraud", "cheat")
ME = "choo-chu"
FU_MIN_N, FU_GOOD, FU_BAD = 10, 0.10, 0.50
FU_BANNED = ("드디어", "이제야", "역시", "겨우", "그나마", "결국", "또다시", "여전히", "ㅋ", "ㅎ")
BUTTONS = lambda pid: {"inline_keyboard": [[{"text": "승인 (게시)", "callback_data": f"approve:{pid}"},
                                              {"text": "거절", "callback_data": f"reject:{pid}"}]]}


def _catalog():
    try:
        return json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def excluded(shop, now=None, path=None):
    """True while the shop is off callouts after a correction (callout_rules.json excluded {shop: until})."""
    return (now or time.time()) < float((rules(path).get("excluded") or {}).get(shop, 0))


def exclude(shop, now=None, path=None, why=""):
    path = path or RULES
    r = rules(path)
    r.setdefault("excluded", {})[shop] = round((now or time.time()) + EXCLUDE_S)
    if why:
        r.setdefault("why", {})[shop] = why
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def candidates(now=None, db=None):
    """{shop: [(seq, ts, sku, name, grade), ...]} meeting the condition (11/12 ~14:40: >= MIN_TOTAL claimed settles
    in 24 h, breakage left out, untrue share >= MIN_SHARE, >= MIN_N untrue; food only with >= FOOD_MIN_N food settles
    >= FOOD_SHARE untrue; not an excluded shop), and the untrue counts left out per reason."""
    from market_db import connect
    now = now or time.time()
    own = db is None
    db = db or connect()
    try:
        # 11/12 ~15:10: the breakage check in Python (a per-row EXISTS on broken over ~58k claimed settles took ~57 s;
        # that long read kept writers from committing: record_cash / strategy tick "database is locked" at 15:05)
        raw = db.execute("SELECT s.seq, s.ts, COALESCE(s.shop, s.shop_url), s.sku, s.name, s.grade, s.claim_honest, "
                         "s.buyer, s.shop_url FROM settles s WHERE s.claim_honest IS NOT NULL AND s.ts>=? "
                         "AND COALESCE(s.shop, s.shop_url) IS NOT NULL AND COALESCE(s.shop, '')!=? ORDER BY s.seq",
                         (now - WINDOW_S, ME)).fetchall()
        last_break = {}
        for buyer, url, sku, ts in db.execute("SELECT buyer, shop_url, sku, ts FROM broken WHERE ts>=?",
                                              (now - WINDOW_S,)):
            k = (buyer, url, sku)
            last_break[k] = max(ts, last_break.get(k, ts))
    finally:
        if own:
            db.close()
    rows = [r[:7] + (int(last_break.get((r[7], r[8], r[3]), -1) >= r[1]),) for r in raw]
    cat = _catalog()
    by, skipped = {}, {"broken": 0, "food": 0, "share": 0, "excluded": 0}
    for seq, ts, shop, sku, name, grade, honest, broken in rows:
        if broken:
            skipped["broken"] += not honest
            continue
        kind = "food" if (cat.get(sku) or {}).get("kind") == "food" else "goods"
        by.setdefault(shop, {"food": [], "goods": []})[kind].append((seq, ts, sku, name, grade, honest))
    out = {}
    for shop, g in by.items():
        f = g["food"]
        f_bad = sum(1 for x in f if not x[5])
        food_ok = len(f) >= FOOD_MIN_N and f_bad / len(f) >= FOOD_SHARE
        if not food_ok:
            skipped["food"] += f_bad
        use = g["goods"] + (f if food_ok else [])
        bad = [x[:5] for x in sorted(use) if not x[5]]
        if not bad:
            continue
        if len(use) < MIN_TOTAL or len(bad) < MIN_N or len(bad) / len(use) < MIN_SHARE:
            skipped["share"] += len(bad)
            continue
        if excluded(shop, now):
            skipped["excluded"] += len(bad)
            continue
        out[shop] = bad
    return out, skipped


def draft(shop, rows):
    listed = ", ".join(f"#{seq} ({sku}, 실제 등급 {grade})" for seq, _, sku, _, grade in rows[:MAX_LISTED])
    more = f" 외 {len(rows) - MAX_LISTED}건" if len(rows) > MAX_LISTED else ""
    text = (f"[정산 기록] {shop}: 최근 24시간 정산 중 시장 ledger가 판매자의 등급 주장을 사실과 다르다고 표시한 건 "
            f"{len(rows)}건 (claim_honest=false). ledger {listed}{more}. 고장 신고 건은 집계에서 뺐습니다"
            f"{'' if FOOD_SHARE_MIN else ' (식품도 제외)'}. "
            f"ledger 번호로 직접 확인할 수 있습니다.")
    low = text.lower()
    bad = [w for w in BANNED if w in low]
    if bad:
        raise ValueError(f"banned words in the draft: {bad}")
    return {"post": text, "refs": [r[0] for r in rows[:5]], "as": "buyer"}


def item_draft(shop, cases):
    """11/11 ~21:00 (person): an item-mismatch callout, facts only. cases: [{ts, order, item, asked_sku, asked_name,
    receipt_sku, receipt_name, receipt_cat, price, result_seq}]: the time, the ordered item, the receipt's item and the
    result ledger number; no guesses, no insults (BANNED checked)."""
    from datetime import datetime
    parts = []
    for c in cases:
        parts.append(f"{datetime.fromtimestamp(c['ts']):%m/%d %H:%M} 주문 {c['order']} (주문 품목 {c['item']}): buyer가 요청한 "
                     f"SKU {c['asked_sku']} ({c['asked_name']}). 가게가 첫 답에서 제시하고 서명한 영수증의 품목은 "
                     f"{c['receipt_sku']} ({c['receipt_name']}, 카테고리 {c['receipt_cat']}), {c['price']:,}원. 영수증 품목이 "
                     f"주문 품목에 해당하지 않아 결제하지 않았습니다. 결과 기록 ledger #{c['result_seq']}")
    text = (f"[정산 기록] {shop}: 주문 품목과 영수증 품목이 다른 건 {len(cases)}건. " + " / ".join(parts)
            + ". ledger 번호로 직접 확인할 수 있습니다.")
    low = text.lower()
    bad = [w for w in BANNED + FU_BANNED if w in low]
    if bad:
        raise ValueError(f"banned words in the draft: {bad}")
    return {"post": text, "refs": [c["result_seq"] for c in cases][:5], "as": "buyer"}


def correction_draft(shop, post_id, c):
    """11/11 ~21:40 (person): a correction of an earlier record post, facts only, as a reply in its thread."""
    from datetime import datetime
    text = (f"[정산 기록 정정] {shop}: 앞 글(#{post_id})의 {datetime.fromtimestamp(c['ts']):%m/%d %H:%M} 주문 {c['order']} 건을 "
            f"정정합니다. {shop}는 첫 메시지에서 품목 {c['receipt_sku']} ({c['receipt_name']})과 가격 {c['price']:,}원을 "
            f"밝혔고, 저희 buyer가 주문 품목({c['item']})에 맞는지 확인하지 않고 수락한 뒤 영수증 단계에서 결제를 "
            f"거절했습니다. 앞 글의 품목 불일치는 가게의 잘못으로 볼 수 없습니다. 저희 쪽은 수락 전 품목 확인을 "
            f"추가했습니다. 결과 기록 ledger #{c['result_seq']}.")
    low = text.lower()
    bad = [w for w in BANNED + FU_BANNED if w in low]
    if bad:
        raise ValueError(f"banned words in the correction: {bad}")
    return {"post": text, "refs": [c["result_seq"]], "as": "buyer", "thread": post_id}


def window(shop, t0, t1, food, db=None):
    """Claimed settles of a shop in [t0, t1): (n, untrue rows [(seq, sku, grade)], honest seqs); breakage left out,
    food only when `food`."""
    from market_db import connect
    own = db is None
    db = db or connect()
    try:
        rows = db.execute("SELECT s.seq, s.sku, s.grade, s.claim_honest, EXISTS (SELECT 1 FROM broken b WHERE "
                          "b.buyer=s.buyer AND b.shop_url=s.shop_url AND b.sku=s.sku AND b.ts>=s.ts) FROM settles s "
                          "WHERE COALESCE(s.shop, s.shop_url)=? AND s.ts>=? AND s.ts<? AND s.claim_honest IS NOT NULL "
                          "ORDER BY s.seq", (shop, t0, t1)).fetchall()
    finally:
        if own:
            db.close()
    cat = _catalog()
    rows = [r for r in rows if not r[4] and (food or (cat.get(r[1]) or {}).get("kind") != "food")]
    return len(rows), [(r[0], r[1], r[2]) for r in rows if r[3] == 0], [r[0] for r in rows if r[3] == 1]


def _pct(k, n):
    return (f"{k / n:.1%}" if k / n < 0.1 else f"{k / n:.0%}") if n else "-"


def followup_draft(shop, t, food, db=None):
    """(kind, post) for the 24 h after a draft sent at t, or (None, numbers) when neither rule holds."""
    n, bad, good = window(shop, t, t + WINDOW_S, food, db)
    n0, bad0, _ = window(shop, t - WINDOW_S, t, food, db)
    k, k0 = len(bad), len(bad0)
    scope = "식품 포함 " if food else ""
    prev = f"직전 24시간은 {scope}정산 {n0}건 중 {k0}건({_pct(k0, n0)})이었습니다."
    if n >= FU_MIN_N and k / n <= FU_GOOD:
        text = (f"[정산 기록 후속] {shop}: 지난 24시간 {scope}등급 주장이 있는 정산 {n}건 중 시장 ledger가 사실과 다르다고 "
                f"표시한 건은 {k}건({_pct(k, n)})입니다. {prev} 예: ledger "
                + ", ".join(f"#{x}" for x in good[:5]) + ". ledger 번호로 직접 확인할 수 있습니다.")
        kind, refs = "improved", good[:5]
    elif n >= MIN_TOTAL and k >= MIN_N and k / n >= FU_BAD:      # 11/12 ~14:40: >= 3 settles too
        listed = ", ".join(f"#{seq} ({sku}, 실제 등급 {grade})" for seq, sku, grade in bad[:MAX_LISTED])
        more = f" 외 {k - MAX_LISTED}건" if k > MAX_LISTED else ""
        text = (f"[정산 기록 후속] {shop}: 지난 24시간 {scope}등급 주장이 있는 정산 {n}건 중 {k}건({_pct(k, n)})을 "
                f"시장 ledger가 사실과 다르다고 표시했습니다. {prev} ledger {listed}{more}. 고장 신고 건은 집계에서 "
                f"뺐습니다. ledger 번호로 직접 확인할 수 있습니다.")
        kind, refs = "relapsed", [b[0] for b in bad[:5]]
    else:
        return None, {"n": n, "untrue": k, "n_before": n0, "untrue_before": k0}
    low = text.lower()
    bad_words = [w for w in BANNED + FU_BANNED if w in low]
    if bad_words:
        raise ValueError(f"banned words in the follow-up: {bad_words}")
    return kind, {"post": text, "refs": refs, "as": "buyer"}


def followups(now=None):
    """Follow-ups due: the latest draft per shop, sent >= 24 h ago, not followed up yet."""
    import decisions
    from depts import analyst
    from market_db import connect
    now = now or time.time()
    d = analyst.db()
    try:
        latest = d.execute("SELECT key, MAX(id) FROM proposals WHERE kind='callout' AND key LIKE 'callout.%' "
                           "AND message_id IS NOT NULL GROUP BY key").fetchall()
        rows = []
        for key, pid in latest:
            ts, value = d.execute("SELECT ts, value FROM proposals WHERE id=?", (pid,)).fetchone()
            if now < ts + WINDOW_S or d.execute("SELECT 1 FROM analyst_meta WHERE k=?", (f"followup:{pid}",)).fetchone():
                continue
            rows.append((key.split(".", 1)[1], pid, ts, value))
    finally:
        d.close()
    sent = []
    cat = _catalog()
    for shop, pid, ts, value in rows:
        refs = json.loads(value).get("refs") or []
        db = connect()
        try:
            skus = [r[0] for r in db.execute(f"SELECT sku FROM settles WHERE seq IN ({','.join('?' * len(refs))})",
                                             refs)] if refs else []
        finally:
            db.close()
        food = any((cat.get(x) or {}).get("kind") == "food" for x in skus)
        kind, out = followup_draft(shop, ts, food)
        d = analyst.db()
        try:
            d.execute("INSERT OR REPLACE INTO analyst_meta VALUES (?, ?)", (f"followup:{pid}", kind or "none"))
            d.commit()
            fu_recent = d.execute("SELECT COUNT(*) FROM proposals WHERE kind='callout' AND key=? AND ts>=?",
                                  (f"followup.{shop}", now - PER_SHOP_S)).fetchone()[0]
        finally:
            d.close()
        if kind is None:
            decisions.record("cs", "callout_followup", shop, out, f"after draft #{pid}: neither rule holds, no post")
            continue
        if fu_recent:
            continue
        if kind == "relapsed" and (recent(shop, now) or excluded(shop, now)):   # 11/12 ~14:40: 72 h, exclusion
            decisions.record("cs", "callout_followup", shop, out["refs"], f"after draft #{pid}: relapse, but a callout "
                             f"in the last 72 h or the shop is excluded: no post")
            continue
        sent.append(send(shop, out, len(out["refs"]), kind=kind))
    return sent


def recent(shop, now):
    from depts import analyst
    d = analyst.db()
    try:
        return d.execute("SELECT COUNT(*) FROM proposals WHERE kind='callout' AND key=? AND ts>=?",
                         (f"callout.{shop}", now - PER_SHOP_S)).fetchone()[0]
    finally:
        d.close()


def send(shop, post, n, kind=None):
    import decisions
    from depts import analyst
    now = time.time()
    if kind == "item":                           # 11/12 ~14:40 (person) 1: our own buyer's judgement is no evidence
        raise ValueError("callout refused: an item-mismatch draft rests on our buyer's judgement, not the market's")
    if kind == "correction":                     # 11/12 ~14:40 (person) 4: corrected -> off callouts for 7 days
        exclude(shop, now, why=f"correction at {now:.0f}")
    d = analyst.db()
    try:
        key = f"followup.{shop}" if kind == "improved" else f"callout.{shop}"   # a relapse counts as a callout
        reason = {"improved": "후속: 개선", "relapsed": "후속: 재발", "item": f"품목 불일치 {n}건",
                  "correction": "정정"}.get(
            kind, f"등급 주장 불일치 {n}건 (24시간)")
        cur = d.execute("INSERT INTO proposals (ts, kind, key, value, reason, evidence, status, expires) VALUES "
                        "(?,?,?,?,?,?,?,?)", (now, "callout", key, json.dumps(post, ensure_ascii=False),
                                              reason, "ledger", "sent", now + EXPIRE_S))
        pid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    title = {"improved": "후속 초안 (개선)", "relapsed": "후속 초안 (재발)", "item": "저격 글 초안 (품목 불일치)",
             "correction": "정정 글 초안"}.get(
        kind, "저격 글 초안")
    text = (f"[{title} #{pid}] {shop}\n\n{post['post']}\n\nbuyer로 게시 (refs {post['refs']}). "
            f"승인하면 다음 buyer 게시 차례에 올라가고, {EXPIRE_S // 3600}시간 안에 응답이 없으면 폐기합니다.")
    res = analyst._call("sendMessage", chat_id=analyst.chat_id(), text=text[:4000], reply_markup=BUTTONS(pid))
    d = analyst.db()
    try:
        d.execute("UPDATE proposals SET message_id=?, asked=2 WHERE id=?", ((res or {}).get("message_id"), pid))
        d.commit()                                   # asked=2: no '나중에' path for a callout
    finally:
        d.close()
    decisions.record("cs", "callout_draft", shop, post["refs"], f"#{pid}: {n} untrue grade claims in 24 h; "
                     f"telegram {'ok' if res else 'failed'}")
    analyst.log(f"callout draft #{pid} for {shop} sent: telegram {'ok' if res else 'failed'}")
    return pid


def tick(now=None):
    """Every CHECK_EVERY_S from analyst_main: one draft per qualifying shop not drafted in the last 24 h."""
    from depts import analyst
    now = now or time.time()
    d = analyst.db()
    try:
        last = d.execute("SELECT v FROM analyst_meta WHERE k='callout_ts'").fetchone()
        if last and now - float(last[0]) < CHECK_EVERY_S:
            return []
        d.execute("INSERT OR REPLACE INTO analyst_meta VALUES ('callout_ts', ?)", (str(now),))
        d.commit()
    finally:
        d.close()
    sent = followups(now)                      # follow-ups first: a relapse draft blocks a second callout today
    shops, _ = candidates(now)
    for shop, rows in shops.items():
        if recent(shop, now):
            continue
        sent.append(send(shop, draft(shop, rows), len(rows)))
    return sent


def recheck(shop, post, db=None):
    """11/12 ~14:40 (person) 3: every listed ledger number read again -> (ok, why). A callout / relapse lists only
    settles of this shop with claim_honest = false, no breakage after, and the SKU / actual grade the draft names."""
    from market_db import connect
    seqs = [int(x) for x in re.findall(r"#(\d+) \(", post["post"])] or list(post.get("refs") or [])
    if not seqs:
        return False, "ledger 번호 없음"
    own = db is None
    db = db or connect()
    try:
        for seq in seqs:
            r = db.execute("SELECT COALESCE(s.shop, s.shop_url), s.sku, s.grade, s.claim_honest, EXISTS (SELECT 1 FROM "
                           "broken b WHERE b.buyer=s.buyer AND b.shop_url=s.shop_url AND b.sku=s.sku AND b.ts>=s.ts) "
                           "FROM settles s WHERE s.seq=?", (seq,)).fetchone()
            if not r:
                return False, f"#{seq} 원장에 없음"
            if r[0] != shop or r[3] != 0 or r[4]:
                return False, f"#{seq}: 가게 {r[0]}, claim_honest {r[3]}, 고장 {bool(r[4])}"
            if f"#{seq} (" in post["post"] and f"#{seq} ({r[1]}, 실제 등급 {r[2]})" not in post["post"]:
                return False, f"#{seq}: 원장은 {r[1]} 실제 등급 {r[2]}"
    finally:
        if own:
            db.close()
    return True, f"{len(seqs)}건 재확인"


def approve(p):
    """HANDLERS['callout']: queue the approved draft as a one-off buyer post (forum_once.json); a callout / relapse
    only after recheck() and not while the shop is excluded (11/12 ~14:40)."""
    post = json.loads(p["value"])
    kind0, shop0 = p["key"].split(".", 1)
    if kind0 == "callout" and not post["post"].startswith("[정산 기록 정정]"):
        if excluded(shop0):
            return f"게시 안 함: {shop0}는 정정 후 7일 제외 중"
        ok, why = recheck(shop0, post)
        if not ok:
            return f"게시 안 함: 원장 재확인 실패 ({why})"
    path = os.path.join(HERE, "forum_once.json")
    try:
        items = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        items = []
    kind, shop = p["key"].split(".", 1)
    key = f"{kind}:{shop}:{p['id']}"
    # callouts and relapse drafts (key callout.*) post first (prio 0, cs._once); praise (followup.*) as any record
    items.append(dict(post, key=key, prio=0 if kind == "callout" else 1))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return f"buyer 게시 대기열에 넣음 ({key})"


def register():
    from depts import analyst
    analyst.HANDLERS["callout"] = approve
