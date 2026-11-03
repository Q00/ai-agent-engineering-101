"""Week 08 — qa.py: the review team's internal audit (HQ 2026-11-04: "do I have to ask before anyone finds these?").
Every team run (through hr.run) it reads the logs and records and reports what is broken, with evidence, before
headquarters has to ask. Code only: a check is a few lines over files the agents already write.

Each finding: severity (red = losing money or rank now, yellow = drifting), area, what, evidence. They go to the top
of the HR report, to Drive week08-reports/ALERTS.md, and into the buyer team's and sales team's briefs.

    python qa.py        # run the audit now and print it
"""
import json
import re
import time
from pathlib import Path

HERE = Path(__file__).parent
LOG = HERE / "logs" / "market-kit.log"
MD = HERE / ".market"
OUT = HERE / "private" / "qa.json"


def _tail(n=40000):
    try:
        return LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _recent(lines, minutes):
    """Log lines from the last `minutes`. The kit stamps HH:MM:SS in KST (the VPS clock is UTC)."""
    k = time.gmtime(time.time() + 9 * 3600)
    now = k.tm_hour * 3600 + k.tm_min * 60 + k.tm_sec
    out = []
    for l in lines:
        m = re.match(r"(\d\d):(\d\d):(\d\d) ", l)
        if m and (now - (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))) % 86400 < minutes * 60:
            out.append(l)
    return out


def _memory(since):
    out = []
    try:
        for line in (HERE / "memory.jsonl").read_text(encoding="utf-8").splitlines()[-5000:]:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("ts", 0) > since:
                out.append(m)
    except OSError:
        pass
    return out


def audit(board=None):
    f = []
    add = lambda sev, area, what, ev: f.append({"severity": sev, "area": area, "what": what, "evidence": ev})
    lines = _tail()
    last30, last60 = _recent(lines, 30), _recent(lines, 60)
    # 1. errors in the log
    errs = [l for l in last30 if "[market] ledger" not in l and re.search(r"Traceback|buyer error|\] skipped: |no advice: |failed: ", l)]
    if errs:
        add("red" if len(errs) >= 5 else "yellow", "code", f"{len(errs)} errors in the last 30 min", errs[-3:])
    # 2. quotes that came back without a price (phantom stock, refusals)
    quotes = [l for l in last60 if "[buyer] [quote]" in l]
    none = [l for l in quotes if l.rstrip().endswith("ask None")]
    if len(quotes) >= 10 and len(none) / len(quotes) > 0.4:
        add("red", "buyer", f"{len(none)} of {len(quotes)} quotes in the last hour had no price (phantom stock?)",
            none[-2:])
    # 3. buyer outcomes
    mem = [m for m in _memory(time.time() - 3600) if m.get("outcome") != "review"]
    if len(mem) >= 20:
        deals = sum(m.get("outcome") == "deal" for m in mem)
        nocand = sum(m.get("outcome") == "no candidates" for m in mem)
        if deals / len(mem) < 0.40:
            add("red", "buyer", f"deal rate {deals / len(mem):.0%} in the last hour ({deals}/{len(mem)})",
                {o: sum(m.get("outcome") == o for m in mem) for o in ("deal", "no deal", "no answer", "no candidates")})
        if nocand / len(mem) > 0.20:
            add("yellow", "buyer", f"{nocand} of {len(mem)} orders found no shop in the last hour", None)
    skipped = [l for l in last30 if "[filter] skip" in l]
    shops = {re.sub(r".*\[filter\] skip ([^:]+):.*", r"\1", l) for l in skipped}
    if len(shops) >= 5:
        add("red", "buyer", f"the buyer skips {len(shops)} shops (memory pauses/bans)", sorted(shops)[:8])
    # 4. payments the kit refused
    try:
        pays = [json.loads(x) for x in (MD / "payments.jsonl").read_text(encoding="utf-8").splitlines()[-50:] if x.strip()]
    except (OSError, ValueError):
        pays = []
    bad = [p for p in pays if not p.get("paid")]
    if len(pays) >= 20 and len(bad) / len(pays) > 0.15:
        why = {}
        for p in bad:
            k = re.sub(r"[\d.]+", "N", (p.get("reason") or "?"))[:60]
            why[k] = why.get(k, 0) + 1
        add("red", "buyer", f"{len(bad)} of the last {len(pays)} payments refused", why)
    # 5. forum posts
    try:
        sent = [json.loads(x) for x in (MD / "outbox.sent.jsonl").read_text(encoding="utf-8").splitlines()[-200:] if x.strip()]
    except (OSError, ValueError):
        sent = []
    posts = [s for s in sent if "post" in (s.get("sent") or {})]
    failed = [s for s in posts if s.get("status") != 200]
    if failed:
        add("yellow", "forum", f"{len(failed)} of the last {len(posts)} posts were not accepted",
            [str(s.get("answer"))[:100] for s in failed[-2:]])
    try:
        fj = json.loads((MD / "forum.json").read_text(encoding="utf-8"))
        fj = fj.get("posts", fj) if isinstance(fj, dict) else fj
        ours = [p for p in fj if p.get("author") == "dragonash" and p.get("role") == "buyer"
                and time.time() - (p.get("ts") or 0) < 3600]
        if len(fj) >= 50 and len(ours) < 6:
            add("yellow", "forum", f"only {len(ours)} buyer posts in the last hour (the market allows 12)", None)
    except (OSError, ValueError):
        pass
    # 6. experiments that do not collect data
    try:
        rt = json.loads((HERE / "private" / "review_team.json").read_text(encoding="utf-8"))
        arms = rt.get("arms") or {}
        if arms and all((a or {}).get("n", 0) == 0 for a in arms.values()) and \
                time.time() - (HERE / "review_team.py").stat().st_mtime > 3 * 3600:
            add("yellow", "forum", "the review team's experiment has no measured post after 3 h", arms)
    except (OSError, ValueError):
        pass
    # 7. team meetings
    try:
        g = json.loads((HERE / "private" / "team_guard.json").read_text(encoding="utf-8"))
        ran = [r for r in g.get("runs", []) if r.get("ran") and time.time() - r.get("ts", 0) < 2 * 3600]
        if len(ran) < 2:
            add("red", "teams", f"only {len(ran)} team meetings in the last 2 h (mode {g.get('mode')})",
                [r.get("note") for r in g.get("runs", [])[-3:]])
    except (OSError, ValueError):
        pass
    # 8. the shop selling under cost or thin
    deals = [re.search(r"deal (G[0-9a-f]{8}) at (\d+) \(cost ([\d.]+)", l) for l in last60 if "[seller] [shop]" in l]
    deals = [(m.group(1), int(m.group(2)), float(m.group(3))) for m in deals if m]
    under = [d for d in deals if d[1] < d[2]]
    if under:
        add("red", "shop", f"{len(under)} sales under cost in the last hour", under[:3])
    thin = [d for d in deals if d[1] < d[2] * 1.05]
    if deals and len(thin) / len(deals) > 0.2:
        add("yellow", "shop", f"{len(thin)} of {len(deals)} sales in the last hour at under 5% margin", thin[:3])
    out = {"ts": time.time(), "at": time.strftime("%m-%d %H:%M"), "findings": f,
           "red": sum(x["severity"] == "red" for x in f), "yellow": sum(x["severity"] == "yellow" for x in f)}
    try:
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return out


def summary():
    """The last audit for the teams' briefs."""
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def markdown(a):
    icon = {"red": "🔴", "yellow": "🟡"}
    if not a["findings"]:
        return f"# 내부 점검 {a['at']}: 이상 없음\n"
    lines = [f"# 내부 점검 {a['at']}: 🔴 {a['red']} · 🟡 {a['yellow']}"]
    for x in a["findings"]:
        lines.append(f"- {icon[x['severity']]} [{x['area']}] {x['what']}"
                     + (f"\n  - 근거: {json.dumps(x['evidence'], ensure_ascii=False)[:300]}" if x["evidence"] else ""))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(markdown(audit()))
