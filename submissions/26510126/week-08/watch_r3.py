"""Round 3 watcher: follows join-r3.log (kit v4, our seller and buyer) and prints only what needs a look,
plus a heartbeat every 30 minutes with sales, purchases, refusals and the shop's balance and profit.

    python watch_r3.py            # for the Monitor tool; one line per event

What needs a look: the kit or the tunnel restarting, a Python traceback, a settle the market refused, our buyer's
payment failing or being refused for a reason other than its own accept policy, bankruptcy, and no sale and no
purchase for 30 minutes once Round 3 trades.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

SHOP_QUIET = 600     # seconds with no negotiation opened at our shop before an alert
LOG = Path("/Users/hoon/Developer/agent-market/join-r3.log")
# every ALERT line also goes to the team's file (11/11 13:4x): the meta reads the last hour's and an urgent one starts
# its next cycle at once (meta_r3.recent_alerts, urgent_since)
ALERTS = Path("/Users/hoon/Developer/ai-agent-engineering-101-week08/submissions/26510126/week-08/logs/alerts_r3.jsonl")
_print = print


def print(*args, **kw):
    _print(*args, **kw)
    line = " ".join(str(a) for a in args)
    if line.startswith("ALERT"):
        try:
            with ALERTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.time(), "text": line[:400]}, ensure_ascii=False) + "\n")
        except OSError:
            pass
W8 = Path("/Users/hoon/Developer/ai-agent-engineering-101-week08/submissions/26510126/week-08")
INV = Path("/Users/hoon/Developer/ai-agent-engineering-101-week08/submissions/26510126/week-08/.market/inventory.json")
BEAT = 1800
HEARTBEAT = 600     # status line (sales, book, rank) every 10 minutes; BEAT stays the no-trade alarm (user, 11/6 17:4x)
QUIET = 600         # the same kind of alert at most once in 10 minutes (counted meanwhile), so a burst cannot flood
# checkout refusals of a unit the seller then sets aside (30 min, then for good) run at about two an hour (11/5):
# a count every two hours is enough to see if the rate climbs
QUIET_BY = {"our checkout refused a buyer": 7200}
IDLE = 600          # the kit log not growing for 10 minutes: the kit may be stuck
RULES = Path(__file__).with_name("rules")   # every version of the market's agent.md, kept when it changes
FOLLOW = 600        # every 10 minutes: follow_r3.py --alerts (generous buyers, cheap shops, false claims, new and once each)
LOOK = [
    (re.compile(r"\[run_kit_r3\] kit exited"), "kit exited (restarting)"),
    (re.compile(r"\[tunnel\].*(did not|error|failed)", re.I), "tunnel problem"),
    (re.compile(r"Traceback \(most recent call last\)"), "traceback"),
    # Claude failing (a usage limit or an outage) shows first as the GPT fallback; both failing as the buyer's code offers
    (re.compile(r"HARNESS: fell back to GPT|HARNESS: fallback to GPT failed|GUARD: no model answer"), "model fallback"),
    # not_settled_before: a buyer agreed with us twice for one order and the market settles it once; the unit stays
    # in our stock (11/5, 9 of them, no loss), so only other reasons are worth a look
    (re.compile(r"\[acp\] settle refused(?!: \['not_settled_before'\])"), "a buyer's settle for our shop was refused"),
    (re.compile(r"\[acp(-tools)?\] payment (not completed|failed)"), "our payment failed"),
    # shop side only (as meta_r3's SETTLE_REFUSED): "checkout refused: 409 ... not in stock" is our buyer at another shop
    (re.compile(r"\[acp\] (?:settle refused(?!: \['not_settled_before'\])|checkout refused: (?:no |unit ))"), "our checkout refused a buyer"),
    (re.compile(r"\[bankrupt\]"), "bankruptcy"),
    (re.compile(r"\[rollback\]"), "rollback"),
]
SOLD = re.compile(r"\[acp\] settled ")
PAID = re.compile(r"\[acp(-tools)?\] paid ")
REFUSED = re.compile(r"\[acp(-tools)?\] not paying: (.*)")


DECISIONS = INV.parent.parent / "logs" / "strategies_r3.jsonl"   # every meta-harness decision, one line each
CODE_LOG = INV.parent.parent / "logs" / "meta-code-r3.log"     # the harness changing its own code (meta_code_r3)


def guard_decision(d, prev):
    """(summary line, alerts) for one meta-harness decision. The user (11/4): the meta-harness runs seller and buyer;
    Claude watches and stops only its bad choices, so every decision is shown and the risky ones are flagged."""
    s, p = d.get("strategy") or {}, (prev or {}).get("strategy") or {}
    changed = {k: (p.get(k), v) for k, v in s.items() if p and p.get(k) != v}
    buyer = d.get("buyer_changed") or {}
    line = (f"META {time.strftime('%H:%M', time.localtime(d.get('time', 0)))}: "
            + (f"shop {json.dumps(changed, ensure_ascii=False)}" if changed else "shop kept")
            + (f"; buyer {json.dumps(buyer)}" if buyer else "") + f" | {str(d.get('reason') or '')[:160]}")
    alerts = []
    # ask at the floor is not flagged: on 11/4 a replay of 192 goods talks gave +4% margin at ask 0.85 against
    # 0.72 for 5 fewer deals (buyers' top offers: median 0.74 of list), a fair trade for the harness to make
    # (11/11 13:3x) the "min_margin under 6%" alert is gone: the stuck and clearance rules decide floors now, and once
    # alerts reached the team (alerts_r3.jsonl) the sales lead raised min_margin 0.02 -> 0.04 on this stale check alone
    if (s.get("warranty_hours") or 0) >= 4 and (p.get("warranty_hours") or 0) < 4:      # once, when it gets there
        alerts.append(f"warranty_hours {s.get('warranty_hours')}: each broken unit refunds the whole price")
    plan = (d.get("before") or {}).get("buyer", {}).get("plan") or {}
    for k, (old, new) in buyer.items():
        plan[k] = new
    if (plan.get("CAP") or 0) >= 0.95:
        alerts.append(f"buyer CAP {plan.get('CAP')}: paying near list leaves no surplus")
    if (plan.get("C_CAP") or 0) > 0.7:
        alerts.append(f"buyer C_CAP {plan.get('C_CAP')}: grade C breaks ~18%, above 0.7 the expected surplus is thin")
    if d.get("harness_ok") is False and (prev or {}).get("harness_ok") is False:
        alerts.append("the meta-harness model failed two cycles in a row: the strategy is not being steered")
    return line, alerts


BENIGN = re.compile(r"was created in a different Context|^\S* ?\[seller\] GeneratorExit|Task was destroyed but it is pending")

def shop_line():
    try:
        inv = json.loads(INV.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "no inventory.json yet"
    units = inv.get("units", [])
    profit = (inv.get("balance") or 0) - (inv.get("start") or 0) - (inv.get("injected") or 0)
    return (f"round {inv.get('round')}, balance {inv.get('balance')}, profit {profit}, "
            f"{len(units)} units at cost {sum(u['cost'] for u in units)}")


def board_rows():
    """Our shop's and buyer's rows on the market's board, numbers only."""
    try:
        b = httpx.get("https://market.ourolabs.ai/api/board", timeout=10).json()
    except (httpx.HTTPError, ValueError):
        return "board unreachable"
    out = []
    for kind in ("shops", "buyers"):
        rows = b.get(kind) or []
        for i, r in enumerate(rows):
            if r.get("name") == "thisjonghoon":
                nums = {k: v for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
                        and k not in ("joined",)}
                out.append(f"{kind[:-1]} #{i + 1}/{len(rows)} {json.dumps(nums, ensure_ascii=False)}")
    return f"round {b.get('current')}; " + ("; ".join(out) or "we are not on the board")


COURSE_SEEN = Path(__file__).with_name("course_commits_seen.json")


def course_changed():
    """New commits on the course repo's lectures that name Round 3 or rules (11/6 15:53: patch 3.5 was announced there
    while agent.md stayed the same, and the watch missed it). The first run only records what is there."""
    try:
        rows = httpx.get("https://api.github.com/repos/Q00/ai-agent-engineering-101/commits", params={"per_page": 10},
                         headers={"Accept": "application/vnd.github+json"}, timeout=15).json()
        msgs = [(c["sha"], c["commit"]["author"]["date"], c["commit"]["message"].split("\n")[0]) for c in rows]
    except Exception:
        return []
    try:
        seen = set(json.loads(COURSE_SEEN.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = None
    COURSE_SEEN.write_text(json.dumps([m[0] for m in msgs]), encoding="utf-8")
    if seen is None:
        return []
    return [f"{d} {m}" for sha, d, m in msgs if sha not in seen and re.search(r"round|rule|patch|midterm|week-0[89]", m, re.I)]


def rules_changed():
    """The market's agent.md, compared with the last kept copy; on a change, keep the new one and return the changed
    lines (the instructor changes rules while the round runs, 11/3)."""
    try:
        text = httpx.get("https://market.ourolabs.ai/agent.md", timeout=15).text
    except httpx.HTTPError:
        return None
    if len(text) < 1000:
        return None                                   # an error page, not the rules
    RULES.mkdir(exist_ok=True)
    kept = sorted(RULES.glob("agent-md-*.md"))
    old = kept[-1].read_text(encoding="utf-8") if kept else ""
    if text == old:
        return None
    (RULES / f"agent-md-{time.strftime('%m%d-%H%M')}.md").write_text(text, encoding="utf-8")
    import difflib
    return [l for l in difflib.unified_diff(old.splitlines(), text.splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and not l.startswith(("+++", "---"))][:20]


def rank_line(state):
    """Players rank (week-08/rank_r3.py; the board's own Players table once it has rows). The goal is first place;
    the instructor's q00 is a floor. Alerts when we take or lose first place, when q00 passes us, or when our rank
    falls by 5 or more since the last heartbeat."""
    import sys
    sys.path.insert(0, "/Users/hoon/Developer/ai-agent-engineering-101-week08/submissions/26510126/week-08")
    try:
        import rank_r3
        s = rank_r3.fetch()
    except Exception as e:
        return f"rank unavailable ({type(e).__name__})", []
    alerts = []
    r, t = s.get("our_rank"), s.get("teacher_rank")
    if r:
        if (r == 1) != bool(state.get("first")) and "first" in state:
            alerts.append(f"ALERT {'we took first place' if r == 1 else 'we lost first place'}: {rank_r3.line(s)}")
        state["first"] = r == 1
        behind = bool(t and t < r)
        if behind and not state.get("behind"):
            alerts.append(f"ALERT q00 (the floor) is ahead of us: {rank_r3.line(s)}")
        state["behind"] = behind
    if state.get("rank") and r and r - state["rank"] >= 5:
        alerts.append(f"ALERT our Players rank fell {state['rank']} -> {r}: {rank_r3.line(s)}")
    state["rank"] = r or state.get("rank")
    return rank_r3.line(s), alerts


def extremes(state):
    """The user (11/6 18:2x): the meta runs aggressively and Claude checks only the extremes, bankruptcy and rule breaks.
    Alerts when cash runs under 3M, when Book (cash + stock at cost) falls 15% under its high since the watch started,
    and when the board shows our shop with a bankruptcy or a grade claim that was not honest."""
    out = []
    try:
        inv = json.loads(INV.read_text(encoding="utf-8"))
        cash = inv.get("balance") or 0
        book = cash + sum(u.get("cost", 0) for u in inv.get("units", []))
    except (OSError, ValueError):
        return out
    state["high"] = max(state.get("high", 0), book)
    if cash < 3_000_000 and not state.get("low_cash"):
        out.append(f"ALERT extreme: cash {cash:,} under 3M (book {book:,})")
    state["low_cash"] = cash < 3_000_000
    if book < 0.85 * state["high"] and not state.get("book_drop"):
        out.append(f"ALERT extreme: Book {book:,} is 15%+ under its high {state['high']:,}")
    state["book_drop"] = book < 0.85 * state["high"]
    try:
        b = httpx.get("https://market.ourolabs.ai/api/board", timeout=10).json()
        me = next((r for r in b.get("shops") or [] if r.get("name") == "thisjonghoon"), None)
    except (httpx.HTTPError, ValueError):
        me = None
    if me:
        bad = (me.get("claims") or 0) - (me.get("honest_claims") or 0)
        if (me.get("bankruptcies") or 0) > state.get("bankrupt", 0):
            out.append(f"ALERT extreme: the board shows our shop bankrupt ({me.get('bankruptcies')})")
        if bad > state.get("dishonest", 0):
            out.append(f"ALERT extreme: {bad} of our grade claims are not honest on the board")
        state["bankrupt"], state["dishonest"] = me.get("bankruptcies") or 0, bad
    # 11/7 10:41-11:05: after a market restart the market listed our shop at a tunnel from before our 01:22 kit restart,
    # so buyers could not reach us (7 talks in 10 min against 74) until the kit was restarted and joined again
    try:
        shops = httpx.get("https://market.ourolabs.ai/api/shops", timeout=10).json()
        shops = shops if isinstance(shops, list) else shops.get("shops", [])
        url = next((x.get("url") for x in shops if x.get("name") == "thisjonghoon"), None)
        # our tunnel is the kit's last "[tunnel] <url>, waiting" line; compared here so the watch needs no tunnel access
        with LOG.open("rb") as f:                    # the log passes 100 MB: its last 30 MB first, the whole log only
            f.seek(max(LOG.stat().st_size - 30_000_000, 0))   # when the kit has not restarted since (11/8 10:55 false alert)
            ours = re.findall(r"\[tunnel\] (https://\S+?),", f.read().decode("utf-8", "replace"))
            if not ours:
                f.seek(0)
                ours = [m.group(1) for line in f if b"[tunnel] " in line
                        for m in [re.search(r"\[tunnel\] (https://\S+?),", line.decode("utf-8", "replace"))] if m]
        ok = None if not (url and ours) else url.rstrip("/") == ours[-1].rstrip("/")
    except (httpx.HTTPError, ValueError, AttributeError, OSError):
        ok = None                                    # the market itself unreachable: the idle and outage alerts cover it
    if ok is False and not state.get("unreachable"):
        out.append(f"ALERT extreme: the market lists our shop at {url}, which does not answer as us: restart the kit to join again")
    state["unreachable"] = ok is False
    return out


KIT_LOG = Path("/Users/hoon/Developer/ai-agent-engineering-101-week08/submissions/26510126/week-08/logs/market-kit.log")
SETTLED_AT = re.compile(r"^(\d\d:\d\d:\d\d) \[acp\] settled .* at (\d+), ledger")


def paid_revenue(now=None):
    """(revenue, mean hourly revenue of the 3 hours before, sales, mean hourly sales before) of our paid sales from the kit log's
    '[acp] settled ... at P' lines. The user (11/9): the meta's misjudgments must be caught at once; on 11/9 a playbook
    cut paid sales 65% and revenue 20% for 2.5 h before anyone noticed."""
    now = now or time.time()
    try:
        with KIT_LOG.open("rb") as f:
            f.seek(max(KIT_LOG.stat().st_size - 12_000_000, 0))
            text = f.read().decode("utf-8", "ignore")
    except OSError:
        return None, None, None, None
    day = time.strftime("%Y-%m-%d ", time.localtime(now))
    last, before, n_last, n_before = 0, 0, 0, 0
    for line in text.splitlines():
        m = SETTLED_AT.match(line)
        if not m:
            continue
        t = time.mktime(time.strptime(day + m.group(1), "%Y-%m-%d %H:%M:%S"))
        if t > now + 60:
            t -= 86400
        age = now - t
        if age < 3600:
            last, n_last = last + int(m.group(2)), n_last + 1
        elif age < 4 * 3600:
            before, n_before = before + int(m.group(2)), n_before + 1
    return last, before / 3, n_last, n_before / 3


def review_anomalies(hours=2):
    """Buyers whose stars on our paid deals sit far below their stars at other shops (11/16: one buyer rated us 1 on
    clean deals for 80 min while rating others 4.07, another rates every shop 2; the supervisor saw it only when the
    user asked why the rank kept falling, since no check looked at reviews by buyer). Also our last hour's mean
    against the board's. Returns ALERT lines."""
    import statistics
    try:
        E = json.loads((W8 / ".market" / "ledger-recent.json").read_text(encoding="utf-8"))
        blocked = set(json.loads((W8 / "blocked_buyers_r3.json").read_text(encoding="utf-8")).get("buyers") or [])
    except (OSError, ValueError):
        return []
    if not E:
        return []
    now = max(e.get("ts", 0) for e in E)
    urls = {(e["data"].get("url") or "").rstrip("/") for e in E if e.get("kind") == "join"
            and e["data"].get("name") == "thisjonghoon" and e["data"].get("role") == "shop"}
    ours, other = {}, {}
    for e in E:
        d = e.get("data") or {}
        if e.get("kind") != "result" or e.get("ts", 0) < now - hours * 3600 or not d.get("verified") \
                or not isinstance(d.get("stars"), int):
            continue
        (ours if str(d.get("shop") or "").rstrip("/") in urls else other).setdefault(d.get("buyer"), []).append(d["stars"])
    out = []
    for b, v in ours.items():
        if b in blocked or len(v) < 5:
            continue
        m, o = statistics.mean(v), other.get(b) or []
        mo = statistics.mean(o) if len(o) >= 5 else None
        if m <= 2.5:                     # targeted or blanket, a low rater costs our stars the same
            out.append(f"ALERT reviews: buyer {b} rated our last {len(v)} paid deals {m:.2f}"
                       + (f" vs {mo:.2f} at other shops ({len(o)})" if mo is not None else "")
                       + " - candidate for week-08/blocked_buyers_r3.json (read per checkout, no restart)")
    return out


def main():
    pos = LOG.stat().st_size if LOG.exists() else 0
    counts = {"sold": 0, "paid": 0, "refused": 0}
    last_trade, last_beat, last_grow, last_follow = time.time(), time.time(), time.time(), 0
    last_open = time.time()          # the last negotiation a buyer opened with our shop
    quiet_since = None               # set while a "no buyer reached our shop" alert is open
    said, held = {}, {}                 # alert kind -> last time printed, alerts held back since
    rank_state, extreme_state = {}, {}
    seen_decisions = None
    code_pos = CODE_LOG.stat().st_size if CODE_LOG.exists() else 0
    print(f"watching {LOG.name}: {shop_line()}", flush=True)
    while True:
        time.sleep(15)
        if LOG.exists():
            size = LOG.stat().st_size
            if size < pos:
                pos = 0
            if size != pos:
                last_grow = time.time()
            with LOG.open(encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                lines = [l.rstrip() for l in f]
                for i, line in enumerate(lines):
                    if SOLD.search(line):
                        counts["sold"] += 1
                        last_trade = time.time()
                    if PAID.search(line):
                        counts["paid"] += 1
                        last_trade = time.time()
                    if REFUSED.search(line):
                        counts["refused"] += 1
                    if "[seller]" in line and " OPEN " in line:
                        last_open = time.time()
                        if quiet_since:          # 11/11 15:34 the team read a lone "no buyer" alert as still true
                            print(f"ALERT resolved: buyers reach our shop again (quiet since "
                                  f"{time.strftime('%H:%M', time.localtime(quiet_since))})", flush=True)
                            quiet_since = None
                    for rx, what in LOOK:
                        if rx.search(line):
                            # the A2A library's telemetry when a buyer hangs up mid-talk (GeneratorExit, "created in
                            # a different Context"): the seller keeps selling, so it is not worth a look (11/4)
                            if what == "traceback" and any(BENIGN.search(l) for l in lines[i:i + 60]):
                                break
                            if time.time() - said.get(what, 0) > QUIET_BY.get(what, QUIET):
                                more = f" (+{held[what]} more since the last one)" if held.get(what) else ""
                                print(f"ALERT {what}{more}: {line[:240]}", flush=True)
                                said[what], held[what] = time.time(), 0
                            else:
                                held[what] = held.get(what, 0) + 1
                            break
                pos = f.tell()
        now = time.time()
        if now - last_grow > IDLE:
            print(f"ALERT kit log idle for {(now - last_grow) / 60:.0f} min: the kit may be stuck", flush=True)
            last_grow = now
        try:
            trading = json.loads(INV.read_text(encoding="utf-8")).get("round", 0) >= 3
        except (OSError, ValueError):
            trading = False
        # 11/11 14:24-14:40 no buyer reached our shop for 15 min (tunnel) while our buyer kept buying, so the
        # "no sale and no purchase" alert never fired; an urgent ("extreme") alert also starts the team's cycle
        if now - last_open > SHOP_QUIET and trading:
            print(f"ALERT extreme: no buyer reached our shop for {(now - last_open) / 60:.0f} min (the tunnel or the "
                  f"listing?): {shop_line()}", flush=True)
            quiet_since = quiet_since or last_open
            last_open = now
        if now - last_trade > BEAT and trading:
            print(f"ALERT no sale and no purchase for {(now - last_trade) / 60:.0f} min: {shop_line()}", flush=True)
            last_trade = now
        if now - last_follow > FOLLOW:
            last_follow = now
            diff = rules_changed()
            if diff:
                print(f"ALERT market rules (agent.md) changed, {len(diff)} lines: " + " | ".join(d[:160] for d in diff), flush=True)
            for c in course_changed():
                print(f"ALERT course repo: {c[:200]}", flush=True)
            try:
                r = subprocess.run([str(Path(__file__).with_name(".venv") / "bin/python"), str(Path(__file__).with_name("follow_r3.py")),
                                    "--alerts"], capture_output=True, text=True, timeout=240)
                for line in r.stdout.splitlines():
                    if line.startswith("FOLLOW "):
                        print(line[:300], flush=True)
            except (OSError, subprocess.SubprocessError):
                pass
        if CODE_LOG.exists() and CODE_LOG.stat().st_size != code_pos:   # every build, gate result, deploy, verdict
            with CODE_LOG.open(encoding="utf-8", errors="replace") as f:
                f.seek(code_pos if CODE_LOG.stat().st_size > code_pos else 0)
                for line in f:
                    if "CODE:" in line or "Traceback" in line:
                        print(("ALERT " if "Traceback" in line or "revert" in line or "strayed" in line or
                               "live folder" in line else "") + line.strip()[:300], flush=True)
                code_pos = f.tell()
        try:                                             # each new meta-harness decision, with its risky parts
            lines = DECISIONS.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        if seen_decisions is None:
            seen_decisions = len(lines)
        for i in range(seen_decisions, len(lines)):
            try:
                d, prev = json.loads(lines[i]), json.loads(lines[i - 1]) if i else None
            except ValueError:
                continue
            line, alerts = guard_decision(d, prev)
            print(line[:400], flush=True)
            for a in alerts:
                print(f"ALERT meta: {a}", flush=True)
        seen_decisions = len(lines)
        if now - last_beat > HEARTBEAT:
            rev, base, n, n_base = paid_revenue(now)
            if rev is not None and n_base and n_base >= 20 and (n < 0.5 * n_base or (rev < 0.5 * base and n < 0.75 * n_base)) \
                    and now - said.get("revenue", 0) > 3600:
                print(f"ALERT paid sales fell: {n} sales, {rev:,} in the last hour against {n_base:.0f} and {base:,.0f} "
                      f"an hour before (check the meta's latest changes)", flush=True)
                said["revenue"] = now
            for a in extremes(extreme_state):
                print(a, flush=True)
            # the supervisor's stated facts against the board, hourly (11/9 16:3x: "our forum rating leads" sat in the
            # standing orders while we were #13 of 40; week-08/claims_r3.py)
            if now - said.get("claims", 0) > 3600:
                said["claims"] = now
                try:
                    r = subprocess.run([sys.executable, "claims_r3.py"], cwd=str(W8), capture_output=True, text=True,
                                       timeout=60)
                    for l in r.stdout.splitlines():
                        if not l.startswith("ok ") and l.strip() != "no claims":
                            print(f"ALERT claim: {l[:240]}", flush=True)
                except Exception as e:
                    print(f"ALERT claim check failed: {e}", flush=True)
            if now - said.get("reviews", 0) > 1800:
                said["reviews"] = now
                for a in review_anomalies():
                    print(a, flush=True)
            last_beat = now
            rl, ra = rank_line(rank_state)
            for a in ra:
                print(a, flush=True)
            print(f"heartbeat {time.strftime('%H:%M')}: sold {counts['sold']}, bought {counts['paid']}, "
                  f"refused by our accept policy {counts['refused']}; {shop_line()}; {rl}", flush=True)


if __name__ == "__main__":
    main()
