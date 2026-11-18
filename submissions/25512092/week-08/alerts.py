"""Messages for the person: urgent alerts and the once-a-day summary.

Every message goes to logs/alerts.jsonl and the console (the kit log). If a Telegram bot is configured it also
goes to Telegram. The bot token and chat id are read only from the environment variables TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID (on Windows also from the user's saved environment, HKCU\\Environment, so `setx` works without
restarting the kit). They are never written to a file, a log or a commit; error messages are cut before any URL.
Without them, file + console only.

The same alert key is sent at most once per REPEAT_S.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "logs", "alerts.jsonl")
REPEAT_S = 3600
_sent = {}


def _env(name):
    v = os.environ.get(name)
    if v:
        return v
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0] or None
    except Exception:
        return None


def _seen(key):
    """Repeat guard across processes: the last time this key went out, from alerts.jsonl."""
    if key in _sent:
        return _sent[key]
    last = 0
    try:
        with open(PATH, encoding="utf-8") as f:
            for line in f:
                if f'"key": "{key}"' in line:
                    try:
                        last = max(last, json.loads(line)["ts"])
                    except (ValueError, KeyError):
                        pass
    except OSError:
        pass
    return last


def telegram(text):
    token, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "off"
    try:
        import httpx
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       data={"chat_id": chat, "text": text[:4000]}, timeout=15)
        return "sent" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        return f"failed: {type(e).__name__}"             # never the exception text: it can carry the URL


AUTO_MODE = os.path.join(HERE, "auto_mode.json")


def held(level, key, now=None):
    """11/10 ~15:00 (person, auto mode until auto_mode.json "until"): only urgent alerts and the scheduled summaries
    (keys starting with one of "pass_keys") go to Telegram; warnings and info are kept in alerts.jsonl (telegram
    "held") and listed in the summaries."""
    try:
        with open(AUTO_MODE, encoding="utf-8") as f:
            a = json.load(f)
    except (OSError, ValueError):
        return False
    if not (a.get("enabled") and (now or time.time()) < float(a.get("until") or 0)):
        return False
    return level != "urgent" and not any(key.startswith(p) for p in a.get("pass_keys") or [])


def brakes_warn_only(now=None):
    """True while auto mode (auto_mode.json) says the remaining brakes only warn (person 11/10 ~15:00)."""
    try:
        with open(AUTO_MODE, encoding="utf-8") as f:
            a = json.load(f)
    except (OSError, ValueError):
        return False
    return bool(a.get("enabled") and a.get("brakes_warn_only") and (now or time.time()) < float(a.get("until") or 0))


def send(level, key, text, log=print, repeat_s=REPEAT_S):
    """level: urgent | daily | info. Returns the Telegram status (off / sent / ...) or 'repeat'.
    The same key goes out at most once per repeat_s (default 1 h)."""
    now = time.time()
    if level != "daily" and now - _seen(key) < repeat_s:
        return "repeat"
    _sent[key] = now
    tg = "held" if held(level, key, now) else telegram(f"[Choo Shop {level}] {text}")
    try:
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(now, 1), "level": level, "key": key, "text": text, "telegram": tg},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass
    log(f"[alert] {level} {key}: {text} (telegram {tg})")
    return tg


KEEPALIVE_LOG = os.path.join(HERE, "logs", "keepalive.log")
SEEN = os.path.join(HERE, "logs", "alerts_seen.json")


def cap_review(seen, log=print, now=None):
    """24 h after a STOCK_CAP change, once: sales in the 24 h after vs the 24 h before, and stock at cost at the
    change vs 24 h later. Sales not up while stock grew -> urgent alert."""
    import sales_report
    from market_db import connect
    now = now or time.time()
    c = sales_report.cap_change()
    if not c or now < c[0] + 86400 or seen.get("cap_review") == c[0]:
        return None
    db = connect()
    try:
        cc = sales_report.cap_compare(db, c[0] + 86400, max_h=24)
    finally:
        db.close()
    seen["cap_review"] = c[0]
    b, a = cc["before"], cc["after"]
    grew = a["stock_end"] is not None and a["stock_start"] is not None and a["stock_end"] > a["stock_start"]
    text = (f"STOCK_CAP {c[1]:,}, 24 h after vs before: sold {b['sales']} -> {a['sales']}, revenue {b['revenue']:,} -> "
            f"{a['revenue']:,}; stock at cost {a['stock_start']} -> {a['stock_end']}")
    import decisions
    if a["sales"] <= b["sales"] and grew:
        decisions.record("qc", "cap_review", "STOCK_CAP", c[1], "sales not up, stock grew: " + text)
        return send("urgent", f"cap_review:{c[0]}", "sales did not rise but stock grew after the cap change. " + text, log)
    decisions.record("qc", "cap_review", "STOCK_CAP", c[1], "ok: " + text)
    log(f"[alert] cap review ok: {text}")
    return "ok"


def watch(reports=None, log=print):
    """Called by restock.py every minute: urgent issues in the department reports, and new keepalive.log lines
    (kit restarted, public card not answering twice or more in a row)."""
    try:
        seen = json.load(open(SEEN, encoding="utf-8"))
    except (OSError, ValueError):
        seen = {"keepalive_lines": None}
    try:
        lines = open(KEEPALIVE_LOG, encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    start = seen.get("keepalive_lines")
    if start is None:
        start = len(lines)                             # first run: only lines from now on
    for ln in lines[start:]:
        if "kit started" in ln:
            send("urgent", "kit_restart", f"keepalive restarted the kit: {ln[:160]}", log)
        elif "not answering (3/" in ln:
            send("urgent", "shop_offline", f"public shop card not answering: {ln[:160]}", log)
        elif "not answering (2/" in ln:                # decision 11/06 (option C): wait 2 min for the kit's re-registration
            t = _ka_ts(ln)
            if t:
                seen.setdefault("pending_2of3", []).append([t, ln[:160]])
    seen["keepalive_lines"] = len(lines)
    try:
        tunnel_check(seen, log)
    except Exception as e:
        log(f"[alert] tunnel check: {type(e).__name__}")
    try:
        forum_check(seen, log)
    except Exception as e:
        log(f"[alert] forum check: {type(e).__name__}")
    try:
        cap_review(seen, log)
    except Exception as e:
        log(f"[alert] cap review: {type(e).__name__}")
    try:
        sofa_check(seen, log)
    except Exception as e:
        log(f"[alert] sofa check: {type(e).__name__}")
    try:
        sofa_warranty_check(seen, log)
    except Exception as e:
        log(f"[alert] sofa warranty check: {type(e).__name__}")
    try:
        volume_guard(log)
    except Exception as e:
        log(f"[alert] volume guard: {type(e).__name__}")
    try:
        endgame_request(seen, log)
    except Exception as e:
        log(f"[alert] endgame request: {type(e).__name__}")
    try:
        haggle_report(seen, log)
    except Exception as e:
        log(f"[alert] haggle report: {type(e).__name__}")
    try:
        claim_report(seen, log)
    except Exception as e:
        log(f"[alert] claim report: {type(e).__name__}: {e}")
    for fn in (post_report, injection_report):        # poster_check: replaced by buyer_poster switch alerts
        try:
            fn(seen, log)
        except Exception as e:
            log(f"[alert] {fn.__name__}: {type(e).__name__}: {e}")
    try:
        avoid_report(seen, log)
    except Exception as e:
        log(f"[alert] avoid report: {type(e).__name__}: {e}")
    try:
        expand_report(seen, log)
    except Exception as e:
        log(f"[alert] expand report: {type(e).__name__}: {e}")
    try:
        subst_report(seen, log)
    except Exception as e:
        log(f"[alert] subst report: {type(e).__name__}: {e}")
    try:                                            # buyer tactic experiment: stop rule + 24 h interim (11/07)
        from depts import buyer_tactic
        buyer_tactic.guard(send, log)
        buyer_tactic.interim(send, log)
    except Exception as e:
        log(f"[alert] buyer tactic: {type(e).__name__}: {e}")
    try:
        buyer_mo_guard(log)
    except Exception as e:
        log(f"[alert] buyer min-offers guard: {type(e).__name__}")
    try:
        seller_check(seen, log)
    except Exception as e:
        log(f"[alert] seller check: {type(e).__name__}")
    for dept, r in (reports or {}).items():
        for i in r.get("issues", []):
            if i.get("level") == "urgent":
                send("urgent", f"dept:{dept}:{i['text'][:30]}", f"{dept}: {i['text']}", log)
    try:
        with open(SEEN, "w", encoding="utf-8") as f:
            json.dump(seen, f)
    except OSError:
        pass


FORUM_DROP = 0.01           # buyer forum rating (Wilson lower bound) down this much between board snapshots


def forum_check(seen, log=print):
    """Replaces tools/patch35_watch.py (option C, after Patch 3.5 went live 11/06 18:05): compare my buyer's
    forum rating in the two newest 30-min board snapshots (board_hist, no extra request); a drop of FORUM_DROP or
    more (a dislike?) -> urgent alert, once per snapshot."""
    from market_db import connect
    db = connect()
    try:
        rows = db.execute("SELECT ts, buyer FROM board_hist ORDER BY ts DESC LIMIT 2").fetchall()
    finally:
        db.close()
    if len(rows) < 2 or seen.get("forum_checked") == rows[0][0]:
        return None
    seen["forum_checked"] = rows[0][0]
    new, old = json.loads(rows[0][1]).get("forum"), json.loads(rows[1][1]).get("forum")
    if new is not None and old is not None and old - new >= FORUM_DROP:
        return send("urgent", f"forum_drop:{int(rows[0][0])}", f"buyer forum rating fell {old:.3f} -> {new:.3f} "
                    f"between {time.strftime('%H:%M', time.localtime(rows[1][0]))} and "
                    f"{time.strftime('%H:%M', time.localtime(rows[0][0]))} (a dislike?)", log)
    return None


SWAPS = os.path.join(HERE, "logs", "tunnel_swaps.jsonl")
REREG_WAIT_S = 120          # a 2/3 miss with a kit re-registration within this: no alert, only the daily count
SWAP_FAIL_S = 180           # a dead tunnel without a re-registration within this: urgent


def _ka_ts(line):
    """'11-06 17:09:02 [keepalive] ...' -> epoch (this year)."""
    m = __import__("re").match(r"(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)", line)
    if not m:
        return None
    y = time.localtime().tm_year
    return time.mktime((y, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), 0, 0, -1))


def kit_tunnel_events(now=None):
    """(tunnel stopped, shop registered again) times from the tail of the newest kit console log (HH:MM:SS lines:
    taken as today, or yesterday when that would be in the future)."""
    import glob
    import re
    now = now or time.time()
    files = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)
    if not files:
        return [], []
    with open(files[-1], "rb") as f:
        f.seek(max(0, os.path.getsize(files[-1]) - 3_000_000))
        tail = f.read().decode("utf-8", errors="replace").splitlines()
    day0 = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    stops, regs = [], []
    for ln in tail:
        m = re.match(r"(\d\d):(\d\d):(\d\d) \[(tunnel|market)\] (.*)", ln)
        if not m:
            continue
        t = day0 + int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if t > now + 60:
            t -= 86400
        if m.group(4) == "tunnel" and "stopped answering" in m.group(5):
            stops.append(t)
        elif m.group(4) == "market" and "registered again" in m.group(5):
            regs.append(t)
    return stops, regs


def tunnel_check(seen, log=print, now=None):
    """Kit tunnel swaps: each 'stopped answering' paired with the next 're-registered' -> logs/tunnel_swaps.jsonl
    (offline = the gap). No re-registration within SWAP_FAIL_S -> urgent. keepalive 2/3 misses wait REREG_WAIT_S:
    a re-registration within that (or up to 3 min before) clears them, otherwise urgent."""
    now = now or time.time()
    stops, regs = kit_tunnel_events(now)
    done = set(seen.get("swaps_logged", []))
    for s in stops:
        if str(int(s)) in done:
            continue
        r = next((x for x in sorted(regs) if s <= x <= s + 600), None)
        if r is not None:
            with open(SWAPS, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": round(s), "registered": round(r), "offline_s": round(r - s)}) + "\n")
            done.add(str(int(s)))
        elif now - s > SWAP_FAIL_S:
            send("urgent", f"tunnel_fail:{int(s)}", f"tunnel stopped at {time.strftime('%H:%M:%S', time.localtime(s))} "
                 f"and the shop was not registered again within {SWAP_FAIL_S // 60} min", log)
            done.add(str(int(s)))
    seen["swaps_logged"] = sorted(done)[-50:]
    keep = []
    for t, line in seen.get("pending_2of3", []):
        if any(t - 180 <= r <= t + REREG_WAIT_S for r in regs):
            continue                                   # the kit swapped the tunnel: daily count only
        if now - t > REREG_WAIT_S:
            send("urgent", "shop_offline", f"public shop card not answering and no re-registration in "
                 f"{REREG_WAIT_S // 60} min: {line}", log)
            continue
        keep.append([t, line])
    seen["pending_2of3"] = keep


def tunnel_swaps(t0, t1):
    """(swaps, offline minutes) in [t0, t1) from logs/tunnel_swaps.jsonl."""
    n, s = 0, 0
    try:
        for ln in open(SWAPS, encoding="utf-8"):
            e = json.loads(ln)
            if t0 <= e["ts"] < t1:
                n += 1
                s += e["offline_s"]
    except (OSError, ValueError):
        pass
    return n, round(s / 60, 1)


def sofa_check(seen, log=print, now=None):
    """Sofa trial reminders: 12 h after the start (interim) and at the end (final, asks for a decision). Once each."""
    import json as _j
    import policy
    import sales_report
    from market_db import connect
    now = now or time.time()
    t0, t1 = policy.get("purchasing", "sofa_trial_start"), policy.get("purchasing", "sofa_trial_end")
    if not t0:
        return None
    cat = _j.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    for tag, due, final in (("mid", t0 + 12 * 3600, False), ("final", t1, True)):
        key = f"sofa_{tag}:{int(t0)}"
        if now >= due and not seen.get(key):
            db = connect()
            try:
                text = sales_report.sofa_trial_text(sales_report.sofa_trial_numbers(db, cat, now), final=final, now=now)
            finally:
                db.close()
            seen[key] = now
            send("daily", key, ("Sofa trial final report" + chr(10) if final else "Sofa trial 12 h report" + chr(10)) + text, log)
    return None


SOFA_REFUND_MAX = 3      # decision 11/07: 3 sofa warranty refunds, or refunds > sofa profit -> warranty off


def sofa_numbers(since, now=None):
    """Sofa results since the rules went on: negotiations, receipts, sales, profit, warranty refunds (n, amount)."""
    from depts import sofa
    from market_db import ME, connect
    now = now or time.time()
    db = connect()
    try:
        deals = db.execute("SELECT sku, outcome FROM shop_deals WHERE ts>=? AND ts<?", (since, now)).fetchall()
        deals = [d for d in deals if sofa.is_sofa(d[0])]
        sales = db.execute("SELECT s.sku, s.amount, m.cost FROM settles s LEFT JOIN my_stock m ON m.unit=s.unit "
                           "WHERE s.shop=? AND s.ts>=? AND s.ts<?", (ME, since, now)).fetchall()
        sales = [x for x in sales if sofa.is_sofa(x[0])]
        # 11/09: refunded holds the amount (> 0 = refunded); only refunds of sales made since `since`, so a sale
        # under an older warranty rule does not count against the current one
        refunds = db.execute("""SELECT b.sku, b.refunded, (SELECT s.ts FROM settles s WHERE s.shop=? AND s.sku=b.sku
                                AND s.buyer=b.buyer AND s.ts<=b.ts ORDER BY s.ts DESC LIMIT 1)
                                FROM broken b WHERE b.shop=? AND b.refunded>0 AND b.ts>=? AND b.ts<?""",
                             (ME, ME, since, now)).fetchall()
        refunds = [(r[0], r[1]) for r in refunds if sofa.is_sofa(r[0]) and r[2] is not None and r[2] >= since]
    finally:
        db.close()
    n = len([d for d in deals if d[1] != "open"])
    rec = len([d for d in deals if d[1] == "receipt"])
    return {"negotiations": n, "receipts": rec, "rate": rec / n if n else None, "sold": len(sales),
            "revenue": sum(x[1] or 0 for x in sales), "profit": sum((x[1] or 0) - (x[2] or 0) for x in sales),
            "refunds": len(refunds), "refund_amount": sum(r[1] or 0 for r in refunds)}


def sofa_text(o):
    rate = f"{o['rate']:.0%}" if o["rate"] is not None else "-"
    return (f"소파: 협상 {o['negotiations']}, 성사 {o['receipts']} ({rate}), 판매 {o['sold']} (매출 {o['revenue']:,}, "
            f"이익 {o['profit']:,}), 보증 환불 {o['refunds']}건 {o['refund_amount']:,}")


def sofa_off_trip(r, now=None):
    """(trip, numbers, rule text). From 11/10 ~11:50 (person), with "off_rule" {window_h, share} in sofa_rules.json: off
    when the sofa warranty refunds of the last window_h (48 h) are over share (30%) of the profit on sofa sales of the
    same window_h (any refund with no positive profit trips). Without it: 3 refunds or refunds > profit since
    warranty_since (the 11/07 rule)."""
    now = now or time.time()
    rule = r.get("off_rule") or {}
    if rule.get("window_h"):
        o = sofa_numbers(now - rule["window_h"] * 3600, now)
        lim = rule["share"] * max(o["profit"], 0)
        return (bool(o["refunds"]) and o["refund_amount"] > lim, o,
                f"최근 {rule['window_h']}시간 소파 환불액 > 소파 이익의 {rule['share']:.0%}")
    o = sofa_numbers(r.get("warranty_since") or r["since"], now)   # 11/09: counted from the grade rule
    return (o["refunds"] >= SOFA_REFUND_MAX or bool(o["refunds"] and o["refund_amount"] > o["profit"]), o,
            "환불 누적 3건 또는 환불액 > 소파 이익")


def sofa_warranty_check(seen, log=print, now=None):
    """C safety: sofa_off_trip -> warranty off in sofa_rules.json (the seller reads it per negotiation) + urgent
    alert. Once."""
    import json as _j
    from depts import sofa
    r = sofa.rules()
    if not (r.get("enabled") and r.get("warranty") and r.get("since")):
        return None
    trip, o, rule = sofa_off_trip(r, now)
    if trip:
        r.update(warranty=False, warranty_off_at=round(time.time()),
                 warranty_off_reason=f"{rule}: {o['refunds']} refunds, {o['refund_amount']:,} vs sofa profit {o['profit']:,}")
        with open(sofa.RULES, "w", encoding="utf-8") as f:
            _j.dump(r, f, ensure_ascii=False)
        send("urgent", f"sofa_warranty_off:{int(r.get('warranty_since') or r['since'])}", f"소파 보증 자동 해제 ({rule}). "
             + sofa_text(o) + ". 가격/코드 흥정 규칙은 유지", log)
    return None


def shop_numbers(t0, t1):
    """My shop in [t0, t1): sales, revenue, profit, closed negotiations, profit per negotiation, cancel rate."""
    from market_db import ME, connect
    db = connect()
    try:
        s = db.execute("SELECT COUNT(*), COALESCE(SUM(s.amount),0), COALESCE(SUM(s.amount-m.cost),0) FROM settles s "
                       "JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND s.ts>=? AND s.ts<?", (ME, t0, t1)).fetchone()
        d = db.execute("SELECT COUNT(*), COALESCE(SUM(outcome='cancelled'),0) FROM shop_deals WHERE outcome!='open' "
                       "AND ts>=? AND ts<?", (t0, t1)).fetchone()
    finally:
        db.close()
    return {"sales": s[0], "revenue": s[1], "profit": s[2], "negotiations": d[0],
            "profit_per_negotiation": round(s[2] / d[0]) if d[0] else None,
            "cancel_rate": d[1] / d[0] if d[0] else None}


def shop_text(o):
    cr = f"{o['cancel_rate']:.0%}" if o["cancel_rate"] is not None else "-"
    return (f"판매 {o['sales']}, 매출 {o['revenue']:,}, 이익 {o['profit']:,}, 협상 {o['negotiations']}, "
            f"협상당 이익 {o['profit_per_negotiation']}, 취소율 {cr}")


VOLUME_CHECK_H = 6
VOLUME_MAX_DROP = 0.30


def volume_guard(log=print, now=None):
    """Volume pricing safety (decision 11/07 ~02:00): once, VOLUME_CHECK_H after 'since', profit of the 6 h after
    vs the 6 h before; down by VOLUME_MAX_DROP or more -> volume_rules.json enabled false (the price rules go back,
    sofa rules untouched) + urgent alert. Same hours one day earlier go in the message for context."""
    from depts import volume
    r = volume.rules()
    now = now or time.time()
    since = r.get("since")
    if not (r.get("enabled") and since) or r.get("checked_at") or now < since + VOLUME_CHECK_H * 3600:
        return None
    span = VOLUME_CHECK_H * 3600
    after, before = shop_numbers(since, since + span), shop_numbers(since - span, since)
    yday = shop_numbers(since - 86400, since - 86400 + span)
    bad = before["profit"] > 0 and after["profit"] < (1 - VOLUME_MAX_DROP) * before["profit"]
    r.update(checked_at=round(now), check_after=after["profit"], check_before=before["profit"], check_bad=bad)
    import decisions
    if bad:
        r["enabled"] = False
        r["reverted_at"] = round(now)
        volume.save(r)
        decisions.record("sales", "volume_revert", "volume_rules", after["profit"],
                         f"profit 6 h after {after['profit']:,} < 70% of 6 h before {before['profit']:,}; "
                         f"price rules back to the old ones (sofa rules kept)")
        send("urgent", f"volume_revert:{int(since)}", "박리다매 가격 규칙 자동 원복 (적용 후 6시간 이익이 직전 6시간 대비 30% 이상 감소)\n"
             f"후 6h: {shop_text(after)}\n전 6h: {shop_text(before)}\n하루 전 같은 시간: {shop_text(yday)}", log)
    else:
        volume.save(r)
        decisions.record("sales", "volume_check", "volume_rules", after["profit"],
                         f"6 h check passed: after {after['profit']:,}, before {before['profit']:,}")
        send("info", f"volume_check:{int(since)}", f"박리다매 6시간 점검 통과\n후 6h: {shop_text(after)}\n"
             f"전 6h: {shop_text(before)}\n하루 전 같은 시간: {shop_text(yday)}", log)
    return bad


ENDGAME_ASK_AT = __import__("datetime").datetime(2026, 11, 14, 9, 0).timestamp()   # 11/14 09:00 KST (person's decision 11/07 ~02:00, bundle 2 E18)


def endgame_request(seen, log=print, now=None):
    """Once at 11/14 09:00: ask the person for the endgame decisions, with the numbers they need."""
    now = now or time.time()
    if now < ENDGAME_ASK_AT or seen.get("endgame_request"):
        return None
    seen["endgame_request"] = now
    import limits
    try:
        inv = json.load(open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8"))
        units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
        food = [u for u in units if u.get("kind") == "food"]
        stock = sum(u.get("cost", 0) for u in units)
        nums = (f"재고 원가 {stock:,} ({len(units)}개), 그중 식품 {sum(u.get('cost', 0) for u in food):,} ({len(food)}개), "
                f"현금 {int(inv.get('balance', 0)):,}")
    except Exception as e:
        nums = f"재고 계산 실패 ({type(e).__name__})"
    left_h = (limits.ROUND_END - now) / 3600
    send("urgent", "endgame_request", "막바지 전략 결정 요청 (11/14 09:00 예약 알림)\n"
         f"라운드 종료까지 {left_h:.0f}시간, 매입 중단은 종료 {limits.RESTOCK_STOP_H}시간 전, 막바지 할인은 종료 48시간 전부터\n"
         f"{nums}\n결정할 것: 1) 식품 매입 축소 (언제부터, 얼마나) 2) 재고 원가 인정 규칙 반영 (막바지 하한, ENDGAME_MIN) "
         "3) 구조 고정 + architecture.tex/pdf, REPORT.md 마감", log)
    import decisions
    decisions.record("ceo", "endgame_request", "telegram", None, "11/14 09:00 scheduled request for the endgame decisions")
    return None


def haggle_numbers(t0, t1):
    """Code vs harness haggling (11/07 experiment), sofas left out: per arm negotiations, success rate, profit per
    negotiation (settled price - cost over all its negotiations), harness calls per negotiation."""
    from market_db import connect
    db = connect()
    try:
        rows = db.execute(
            "SELECT h.haggle, d.outcome, d.cost, h.harness_calls, (SELECT s.amount FROM settles s WHERE s.unit=d.unit "
            "AND s.ts>=d.ts - 60 ORDER BY s.ts LIMIT 1) FROM shop_haggle h JOIN shop_deals d ON d.task=h.task "
            "WHERE h.ts>=? AND h.ts<? AND h.haggle IS NOT NULL AND COALESCE(h.rule,'')!='sofa'", (t0, t1)).fetchall()
    finally:
        db.close()
    out = {}
    for arm in ("code", "harness"):
        x = [r for r in rows if r[0] == arm]
        n = len(x)
        out[arm] = {"n": n, "deal_rate": round(sum(r[1] == "receipt" for r in x) / n, 3) if n else None,
                    "profit_per_neg": round(sum((r[4] - r[2]) for r in x if r[4] is not None) / n) if n else None,
                    "calls_per_neg": round(sum(r[3] or 0 for r in x) / n, 2) if n else None}
    return out


def haggle_report(seen, log=print, now=None):
    """Once, 24 h after code_haggle.json 'since'."""
    now = now or time.time()
    try:
        j = json.load(open(os.path.join(HERE, "code_haggle.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    since = j.get("since")
    if not (j.get("enabled") and since) or now < since + 86400 or seen.get(f"haggle_report:{int(since)}"):
        return None
    seen[f"haggle_report:{int(since)}"] = now
    o = haggle_numbers(since, since + 86400)
    f = lambda a: (f"{a}: 협상 {o[a]['n']}, 성사율 {o[a]['deal_rate']}, 협상당 이익 {o[a]['profit_per_neg']}, "
                   f"하네스 호출/협상 {o[a]['calls_per_neg']}")
    import decisions
    decisions.record("sales", "code_haggle_report", "code_haggle", o, "24 h result")
    return send("daily", f"haggle_report:{int(since)}", "전 품목 코드 흥정 30% 실험 24시간 결과 (소파 제외)\n"
                + f("code") + "\n" + f("harness"), log)


def high_turnover(t0, t1):
    """High-price units (cost > HIGH_MIN) supplied in [t0, t1): bought, sold by t1, hours supply -> sale."""
    from market_db import ME, connect
    from sales_report import HIGH_MIN
    db = connect()
    try:
        rows = db.execute("SELECT m.unit, m.supplied_at, (SELECT MIN(s.ts) FROM settles s WHERE s.unit=m.unit AND "
                          "s.shop=? AND s.ts<?) FROM my_stock m WHERE m.cost>? AND m.supplied_at>=? AND m.supplied_at<?",
                          (ME, t1, HIGH_MIN, t0, t1)).fetchall()
    finally:
        db.close()
    hrs = sorted((s - a) / 3600 for _, a, s in rows if s)
    db = connect()                                  # 11/07 ~17:55: sales amount in the window too
    try:
        n, rev = db.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM settles WHERE shop=? AND ts>=? AND ts<?",
                            (ME, t0, t1)).fetchone()
        hrev = db.execute("SELECT COALESCE(SUM(s.amount), 0) FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE "
                          "s.shop=? AND s.ts>=? AND s.ts<? AND m.cost>?", (ME, t0, t1, HIGH_MIN)).fetchone()[0]
    finally:
        db.close()
    return {"bought": len(rows), "sold": len(hrs), "sales": n, "revenue": rev, "high_revenue": hrev,
            "median_h": round(hrs[len(hrs) // 2], 1) if hrs else None,
            "mean_h": round(sum(hrs) / len(hrs), 1) if hrs else None}


def poster_check(seen, log=print, now=None):
    """The buyer poster (analyst process) writes a heartbeat every 5 s; older than 15 min -> no buyer posts: urgent."""
    now = now or time.time()
    try:
        hb = json.load(open(os.path.join(HERE, "logs", "buyer_post_state.json"), encoding="utf-8")).get("heartbeat", 0)
    except (OSError, ValueError):
        return None
    if now - hb > 900:
        return send("urgent", "buyer_poster_down", f"buyer 글 게시기 멈춤: 마지막 동작 {int((now - hb) / 60)}분 전 "
                    "(분석관 프로세스 확인). 그동안 buyer 글 없음", log, repeat_s=3600)
    return None


def injection_report(seen, log=print, now=None):
    """Once, 24 h after injection_rules.json 'since' (person's decision 11/07 ~19:00): receipts the buyer refused
    (count, amount, by flag) and negotiations the code finished after an injection (count, outcome, paid / list)."""
    now = now or time.time()
    try:
        since = json.load(open(os.path.join(HERE, "injection_rules.json"), encoding="utf-8")).get("since")
    except (OSError, ValueError):
        return None
    if not since or now < since + 86400 or seen.get(f"injection_report:{int(since)}"):
        return None
    seen[f"injection_report:{int(since)}"] = now
    import sqlite3
    t1 = since + 86400
    ref, amount, flags, no_avg = 0, 0, {}, 0
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if '"injection_no_avg"' in ln and since <= json.loads(ln)["ts"] < t1:
                no_avg += 1                         # 11/07 ~19:25: walked without an offer (no market average)
            if '"receipt_refused"' in ln:
                d = json.loads(ln)
                if since <= d["ts"] < t1:
                    ref += 1
                    amount += int(d.get("value") or 0)
                    k = (d.get("reason") or "").split(":")[0]
                    flags[k] = flags.get(k, 0) + 1
    import dbutil  # 11/12 ~15:15: busy 10 s + retries while db_safe.json is on
    db = dbutil.connect(os.path.join(HERE, "market.db"))
    try:
        rows = db.execute("SELECT outcome, reason, price, list_price FROM buyer_deals WHERE buyer_case='injection_code' "
                          "AND ts>=? AND ts<?", (since, t1)).fetchall()
    finally:
        db.close()
    deals = [r for r in rows if r[0] == "deal"]
    ratio = [r[2] / r[3] for r in deals if r[2] and r[3]]
    o = {"refused": ref, "refused_amount": amount, "by_flag": flags, "injection_negotiations": len(rows),
         "injection_no_avg": no_avg,
         "outcomes": {k: sum(1 for r in rows if r[0] == k) for k in {r[0] for r in rows}},
         "paid_vs_list": round(sum(ratio) / len(ratio), 3) if ratio else None}
    import decisions
    decisions.record("buyer", "injection_report", "buyer_biz", o, "24 h result")
    return send("daily", f"injection_report:{int(since)}",
                f"인젝션 보완 24시간 결과\n거절한 영수증: {ref}건, {amount:,}원 {flags}\n"
                f"인젝션 감지 후 코드가 끝낸 협상: {len(rows)}건, 결과 {o['outcomes']}, 결제가/정가 {o['paid_vs_list']}; "
                f"그중 시장 평균이 없어 제안 없이 walk {no_avg}건", log)


def post_report(seen, log=print, now=None):
    """Once, 24 h after buyer_post_rule.json 'since' (person's decision 11/07 ~17:10): buyer posts per day, likes per
    post by kind (forum archive, the posts it saw), the board's buyer forum score; the 24 h after vs before."""
    now = now or time.time()
    try:
        since = json.load(open(os.path.join(HERE, "buyer_post_rule.json"), encoding="utf-8")).get("since")
    except (OSError, ValueError):
        return None
    if not since or now < since + 86400 or seen.get(f"post_report:{int(since)}"):
        return None
    seen[f"post_report:{int(since)}"] = now
    import re
    import sqlite3
    import dbutil  # 11/12 ~15:15: busy 10 s + retries while db_safe.json is on
    db = dbutil.connect(os.path.join(HERE, "market.db"))

    def win(t0, t1):
        n, kinds = 0, {}
        with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
            for ln in f:
                if '"action": "post"' in ln and '"target": "buyer' in ln:
                    d = json.loads(ln)
                    if t0 <= d["ts"] < t1:
                        n += 1
        for text, likes in db.execute("SELECT text, likes FROM forum_posts WHERE author='choo-chu' AND role='buyer' "
                                      "AND ts>=? AND ts<?", (t0, t1)):
            m = re.match(r"\[([^\]]+)\]", text or "")
            k = m.group(1) if m else "기타"
            a = kinds.setdefault(k, [0, 0])
            a[0] += 1
            a[1] += likes or 0
        forum = [json.loads(b).get("forum") for ts, b in db.execute(
            "SELECT ts, buyer FROM board_hist WHERE ts>=? AND ts<? ORDER BY ts", (t0, t1)) if b]
        return {"posts": n, "likes": {k: {"posts": v[0], "per_post": round(v[1] / v[0], 2)} for k, v in kinds.items()},
                "forum_start": forum[0] if forum else None, "forum_end": forum[-1] if forum else None}
    a, b = win(since, since + 86400), win(since - 86400, since)
    db.close()
    import decisions
    decisions.record("cs", "post_report", "buyer_poster", {"after": a, "before": b}, "24 h result")
    f = lambda w: (f"글 {w['posts']}개, forum 점수 {w['forum_start']} -> {w['forum_end']}, 유형별 글당 좋아요 "
                   + ", ".join(f"{k} {v['per_post']} ({v['posts']}개 집계)" for k, v in w["likes"].items()))
    return send("daily", f"post_report:{int(since)}", "buyer 글 조정 24시간 결과 (빈 슬롯 채우기, 시세 리포트 72분마다)\n"
                f"후: {f(a)}\n전: {f(b)}", log)


def avoid_report(seen, log=print, now=None):
    """Once, 24 h after avoid_rule.json 'since' (person's decision 11/07 ~16:10: avoid a shop for untrue grade claims
    only with >= 2 of them AND >= 5% of its settles): buyer deal rate, ledger skips, missed deals, deals with the shops
    no longer avoided; the 24 h after vs the 24 h before (tools/avoid_stats.py)."""
    now = now or time.time()
    try:
        since = json.load(open(os.path.join(HERE, "avoid_rule.json"), encoding="utf-8")).get("since")
    except (OSError, ValueError):
        return None
    if not since or now < since + 86400 or seen.get(f"avoid_report:{int(since)}"):
        return None
    seen[f"avoid_report:{int(since)}"] = now
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import avoid_stats
    a, b = avoid_stats.window(since, since + 86400), avoid_stats.window(since - 86400, since)
    import decisions
    decisions.record("buyer", "avoid_report", "market_db.BAD_DISHONEST_SHARE", {"after": a, "before": b}, "24 h result")
    f = lambda w: (f"주문 {w['orders']}, 성사율 {w['deal_rate']}, 성사 {w['deals']}, 원장 플래그로 건너뛴 주문 "
                   f"{w['ledger_skips']}, 놓친 거래 {w['missed']}, 회피 해제 가게와 성사 {w['unblocked_deals']}")
    return send("daily", f"avoid_report:{int(since)}", "회피 기준 변경(2건 이상 + 정산 5% 이상) 24시간 결과\n"
                f"후: {f(a)}\n전: {f(b)}", log)


def expand_report(seen, log=print, now=None):
    """Once, 6 h after buy_expand.json 'since' (person's decision 11/07 ~14:45: high_max 8 -> 18, my_share
    0.25 -> 0.5): high-price turnover (supply -> sale) in the 6 h after vs the 6 h before, unsold high-price units."""
    now = now or time.time()
    try:
        j = json.load(open(os.path.join(HERE, "buy_expand.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    since = j.get("since")
    if not since or now < since + 6 * 3600 or seen.get(f"expand_report:{int(since)}"):
        return None
    seen[f"expand_report:{int(since)}"] = now
    from sales_report import HIGH_MIN
    after, before = high_turnover(since, since + 6 * 3600), high_turnover(since - 6 * 3600, since)
    try:
        inv = json.load(open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8"))
    except (OSError, ValueError):
        inv = {}
    high = [u for u in inv.get("units", []) if u.get("grade") != "expired" and (u.get("cost") or 0) > HIGH_MIN]
    old = [u for u in high if now - (u.get("supplied_at") or now) > 6 * 3600]
    s2 = j.get("since2")              # 11/07 ~16:05: STOCK_CAP 15M, high-price units 28, share 0.6 (after 17:47)
    new = high_turnover(s2, now) if s2 and s2 < now else None
    import decisions
    o = {"after": after, "before": before, "unsold_high": len(high), "unsold_high_over_6h": len(old),
         "unsold_high_cost": sum(u.get("cost", 0) for u in high), "since_new_settings": new}
    decisions.record("purchasing", "expand_report", "buy_expand", o, "6 h result")
    f = lambda w: (f"입고 {w['bought']}, 판매 {w['sold']}, 입고->판매 중앙 {w['median_h']} h / 평균 {w['mean_h']} h; "
                   f"가게 매출 {w['revenue']:,} ({w['sales']}건), 그중 고가 매출 {w['high_revenue']:,}")
    s2_line = (f"새 설정(STOCK_CAP 15M, 고가 28개, 비중 60%) {time.strftime('%H:%M', time.localtime(s2))} 이후: "
               f"{f(new)}\n") if new else ""
    guard_n = 0                                     # 11/07 ~18:20: high-price buys the guard cancelled (0 before it)
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as fh:
        for ln in fh:
            if '"action": "buy_guard_skip"' in ln and json.loads(ln)["ts"] >= since:
                guard_n += 1
    s2_line += f"고가 매입 가드로 취소된 매입: {guard_n}건\n"
    return send("daily", f"expand_report:{int(since)}",
                "매입 확대(high_max 8->18, MY_SHARE 0.25->0.5) 6시간 결과\n"
                f"고가 적용 후 6h: {f(after)}\n고가 적용 전 6h: {f(before)}\n" + s2_line +
                f"미판매 고가 지금 {len(high)}개 (원가 {o['unsold_high_cost']:,}), 그중 6시간 넘은 것 {len(old)}개", log)


def subst_report(seen, log=print, now=None):
    """Once, 6 h after substitute.json 'since' (person's decision 11/07 ~10:00): substitute offers, choices / receipts /
    settles by tier, 'receipt does not match' reviews in those 6 h vs the 6 h before."""
    now = now or time.time()
    try:
        j = json.load(open(os.path.join(HERE, "substitute.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    since = j.get("since")
    if not (j.get("enabled") and since) or now < since + 6 * 3600 or seen.get(f"subst_report:{int(since)}"):
        return None
    seen[f"subst_report:{int(since)}"] = now
    from depts import substitute
    o = substitute.numbers(since, since + 6 * 3600)
    import decisions
    decisions.record("sales", "subst_report", "substitute", o, "6 h result")
    t = o["by_tier"]
    return send("daily", f"subst_report:{int(since)}",
                f"대체 제안 6시간 결과: 대체 제안 {o['offers']}건\n"
                + "\n".join(f"{k}순위: 제시 {v['offered']}, 수락 {v['chosen']}, 영수증 {v['receipt']}, 결제 {v['settled']}"
                             for k, v in t.items())
                + f"\n영수증 불일치 리뷰: {o['mismatch_reviews']} (적용 전 6시간 {o['mismatch_reviews_before']})"
                + f"\n카테고리 주문 'receipt names' 불만: {o['category_complaints']} (적용 전 6시간 "
                  f"{o['category_complaints_before']})"
                + "\n등급 명시 실험 중간: " + json.dumps(__import__("depts.receipt_exp", fromlist=["x"]).claim_numbers(
                    since, since + 6 * 3600), ensure_ascii=False), log)


def claim_report(seen, log=print, now=None):
    """Once, 24 h after receipt_rules.json 'since': grade claim arms (non-food) by deal rate and stars."""
    now = now or time.time()
    try:
        j = json.load(open(os.path.join(HERE, "receipt_rules.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    since = j.get("since")
    if not (j.get("claim_share") and since) or now < since + 86400 or seen.get(f"claim_report:{int(since)}"):
        return None
    seen[f"claim_report:{int(since)}"] = now
    from depts import receipt_exp
    o = receipt_exp.claim_numbers(since, since + 86400)
    import decisions
    decisions.record("sales", "claim_report", "receipt_rules", o, "24 h result")
    f = lambda a: (f"{a}: 협상 {o[a]['n']}, 성사율 {o[a]['deal_rate']}, 결제 {o[a]['settled']}, "
                   f"별점 {o[a]['stars']} (n={o[a]['stars_n']})")
    return send("daily", f"claim_report:{int(since)}", "비식품 등급 명시 실험 24시간 결과\n" + f("claim") + "\n"
                + f("none") + f"\n카테고리 영수증 발행 협상: {o['category_receipts']}", log)


MO_SHIFT = os.path.join(HERE, "logs", "buyer_mo_shift.json")


def buyer_mo_guard(log=print):
    """Buyer min-offers shift (decision 11/07 ~02:00: weight 1 -> 0.9): when the buyer's Saved rank (board_hist,
    every 30 min) is worse than at the shift in 2 snapshots in a row -> weights back to 0.8 / 0.2 + alert. Once."""
    import json as _j
    try:
        st = _j.load(open(MO_SHIFT))
    except (OSError, ValueError):
        return None
    if st.get("done"):
        return None
    from market_db import connect
    db = connect()
    try:
        rows = db.execute("SELECT ts, buyer FROM board_hist WHERE ts>? ORDER BY ts DESC LIMIT 2",
                          (st["since"],)).fetchall()
        ranks = [(_j.loads(b) or {}).get("saved_rank") for _, b in rows]
        if len(ranks) == 2 and all(x is not None and x > st["base_rank"] for x in ranks):
            now = time.time()
            for v, w in (("1", 0.8), ("3", 0.2)):
                db.execute("INSERT INTO variant_weights VALUES (?,?,?,?,?,?,?)",
                           (now, "buyer_min_offers", v, w, None, 0, "revert: Saved rank fell after the 0.9 shift"))
            db.commit()
            st.update(done=True, reverted_at=round(now), ranks=ranks)
            _j.dump(st, open(MO_SHIFT, "w"))
            import decisions
            decisions.record("buyer", "min_offers_revert", "buyer_min_offers", {"1": 0.8, "3": 0.2},
                             f"Saved rank {ranks} worse than {st['base_rank']} at the shift, 2 snapshots in a row")
            send("urgent", "mo_revert", f"buyer 최소 제안 1회 비중 0.9 -> 0.8 원복: Saved 순위 {st['base_rank']}위 -> "
                 f"{ranks[-1]}, {ranks[0]}위", log)
    finally:
        db.close()
    return None


UTIL_ALERT = 0.70        # seller busy share in the last full hour (decision 11/06: tell the person above 70%)


def seller_utilization(now=None):
    """Share of the last full hour the seller spent inside harness calls (estimate). The seller calls the harness
    synchronously, one at a time; each '[seller] [harness]' line is one finished call. Call length = the median gap
    between back-to-back answers (gaps under 20 s) in that hour, so busy share = calls x that length / 3600."""
    import glob
    import re
    now = now or time.time()
    h1 = now // 3600 * 3600
    h0 = h1 - 3600
    files = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)
    if not files:
        return None
    path = files[-1]
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - 4_000_000))             # the last ~4 MB covers more than an hour
        tail = f.read().decode("utf-8", errors="replace").splitlines()
    day0 = time.localtime(h0)
    base = time.mktime((day0.tm_year, day0.tm_mon, day0.tm_mday, 0, 0, 0, 0, 0, -1))
    ts = []
    for ln in tail:
        m = re.match(r"(\d\d):(\d\d):(\d\d) \[seller\] \[harness\]", ln)
        if m:
            t = base + int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if h0 <= t < h1:
                ts.append(t)
    if len(ts) < 2:
        return {"hour": h0, "calls": len(ts), "call_s": None, "busy": 0.0}
    gaps = sorted(b - a for a, b in zip(ts, ts[1:]) if 0 < b - a < 20)
    call_s = gaps[len(gaps) // 2] if gaps else 6.5
    return {"hour": h0, "calls": len(ts), "call_s": call_s, "busy": min(1.0, len(ts) * call_s / 3600)}


def seller_check(seen, log=print):
    """Once per hour: alert when the seller was busy more than UTIL_ALERT of the last full hour."""
    h = time.time() // 3600 * 3600 - 3600
    if seen.get("util_hour") == h:
        return None
    seen["util_hour"] = h
    u = seller_utilization()
    if not u:
        return None
    import decisions
    decisions.record("qc", "seller_util", time.strftime("%m-%d %H:00", time.localtime(u["hour"])), round(u["busy"], 3),
                     f"{u['calls']} harness calls x {u['call_s']} s")
    try:                                            # decision 11/07 ~02:00: after volume pricing went on, one hour
        from depts import volume                    # over UTIL_ALERT turns concurrency on (flag read per call)
        since = volume.rules().get("since")
        flag_path = os.path.join(HERE, "seller_concurrency.json")
        flag = json.load(open(flag_path)) if os.path.exists(flag_path) else {}
        if since and u["hour"] >= since // 3600 * 3600 and u["busy"] > UTIL_ALERT and not flag.get("enabled"):
            flag.update(enabled=True, limit=flag.get("limit", 3), auto_on=round(time.time()),
                        auto_reason=f"{time.strftime('%H:00', time.localtime(u['hour']))} busy {u['busy']:.0%}")
            json.dump(flag, open(flag_path, "w"))
            decisions.record("sales", "concurrency_on", "seller_concurrency", flag["limit"],
                             f"auto: seller busy {u['busy']:.0%} in {time.strftime('%m-%d %H:00', time.localtime(u['hour']))} "
                             f"> {UTIL_ALERT:.0%} after volume pricing; up to {flag['limit']} harness calls at once")
            send("urgent", f"conc_auto:{int(u['hour'])}", f"seller 사용률 {u['busy']:.0%} "
                 f"({time.strftime('%H:00', time.localtime(u['hour']))}) > 70%: 동시 처리(최대 {flag['limit']}개) 자동 켬, "
                 "재시작 없음", log)
    except Exception as e:
        log(f"[alert] concurrency auto-on: {type(e).__name__}: {e}")
    if u["busy"] > UTIL_ALERT:
        return send("urgent", f"seller_util:{int(u['hour'])}",
                    f"seller busy {u['busy']:.0%} of {time.strftime('%H:00', time.localtime(u['hour']))} "
                    f"({u['calls']} harness calls x ~{u['call_s']} s, one at a time): consider concurrent harness calls",
                    log)
    return u
