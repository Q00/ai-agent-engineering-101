"""Read-only prototype of the attempt-2 situation briefing (2026-11-05). Other players are hashed; owner fields,
URLs and names of others are never printed."""
import hashlib, json, os, statistics as st, time
from collections import Counter, defaultdict
os.environ["TZ"] = "Asia/Seoul"; time.tzset()
os.chdir(os.path.expanduser("~/ai-agent-engineering-101/submissions/25520013/week-09"))
pol = json.load(open("policy.json", encoding="utf-8")); login = ((pol.get("owner") or {}).get("github") or "").lower()
END = time.mktime((2026, 11, 17, 23, 59, 0, 0, 0, -1)); now = time.time()
bd = sorted(int(p.split(".")[0]) for p in os.listdir("icrl/state/board") if p.split(".")[0].isdigit())
L = lambda t: json.load(open(f"icrl/state/board/{t}.json", encoding="utf-8"))
b = L(bd[-1]); t6 = min(bd, key=lambda t: abs(t - (bd[-1] - 6 * 3600))); b6 = L(t6); dt = (bd[-1] - t6) / 3600
hid = lambda s: "h" + hashlib.sha1(str(s).encode()).hexdigest()[:6]
def mine(r): return bool(login) and login in json.dumps({k: r.get(k) for k in ("owner", "player", "github")}, ensure_ascii=False).lower()
f = lambda x: "-" if not isinstance(x, (int, float)) else (f"{x/1e6:.1f}M" if abs(x) >= 1e5 else f"{x:.3g}")
print(f"[시각] {time.strftime('%m-%d %H:%M', time.localtime(now))} KST, 회차 종료까지 {(END-now)/3600:.0f}시간, 순위표 기준 {time.strftime('%H:%M', time.localtime(bd[-1]))}")
print("[목표] 최종 순위표 total 1위")
P = sorted(b["players"], key=lambda p: -(p.get("total") or 0)); me = next((p for p in P if mine(p)), None)
r = P.index(me) + 1 if me else None
print(f"[순위] 참가자 {len(P)}명 중 {r}위, total {me['total']:.3f} | 1위 {P[0]['total']:.3f} (차이 {P[0]['total']-me['total']:.3f}), 2위 {P[1]['total']:.3f}, 10위 {P[min(9,len(P)-1)]['total']:.3f}")
print("  상위 5명:", ", ".join(f"{'우리' if mine(p) else hid((p.get('shop') or {}).get('name') or (p.get('buyer') or {}).get('name'))} {p['total']:.3f} (가게 {(p.get('shop') or {}).get('all') or 0:.2f}, 구매자 {(p.get('buyer') or {}).get('all') or 0:.2f})" for p in P[:5]))
def side(rows, rows6, keys, label):
    meR = next((x for x in rows if mine(x)), None)
    if not meR: print(f"[{label}] 우리 행 없음"); return
    me6 = next((x for x in rows6 if mine(x)), {}) or {}
    top = max(rows, key=lambda x: x.get("all") or 0)
    print(f"[{label}] 우리 all {meR.get('all') or 0:.3f}, {label} 1위 all {top.get('all') or 0:.3f}")
    for k in keys:
        v = meR.get(k); vals = sorted((x.get(k) for x in rows if isinstance(x.get(k), (int, float))), reverse=True)
        if not isinstance(v, (int, float)) or not vals: print(f"  {k}: {f(v)}"); continue
        rk = 1 + sum(1 for x in vals if x > v); v6 = me6.get(k)
        rate = f", 6시간 평균 {f((v - v6) / dt)}/h" if isinstance(v6, (int, float)) and dt else ""
        print(f"  {k}: {f(v)} ({len(vals)}곳 중 {rk}위, 1위 {f(vals[0])}, 중앙 {f(st.median(vals))}{rate})")
side(b["shops"], b6.get("shops") or [], ["book", "revenue", "stars", "stars_rank", "deals", "profit", "stock_cost", "stock_units", "refunds", "broken", "claims", "visits", "share"], "가게")
side(b["buyers"], b6.get("buyers") or [], ["surplus", "forum", "posts", "deals"], "구매자")
inv = json.load(open(".market/inventory.json", encoding="utf-8")); cat = json.load(open(".market/catalog.json", encoding="utf-8"))
u = inv["units"]; byc = Counter((cat.get(x.get("sku")) or {}).get("category", "?") for x in u)
exp24 = sum(1 for x in u if x.get("expires_at") and x["expires_at"] - now < 86400)
print(f"[가게 상태] 잔고 {f(inv['balance'])} (시작 {f(inv['start'])}, 투입 {f(inv['injected'])}), 파산선 {f(inv['bankrupt_line'])}, 남은 파산 {inv['bankruptcies_left']}회, 보관비 {f(inv['storage_per_day'])}/일")
print(f"  재고 {len(u)}개: " + ", ".join(f"{c} {n}" for c, n in byc.most_common(6)) + f" | 24시간 안에 유통기한 끝 {exp24}개")
dec = Counter()
with open(".r3/metrics.jsonl", "rb") as fh:
    fh.seek(0, 2); fh.seek(max(0, fh.tell() - 30_000_000)); fh.readline()
    for l in fh:
        if b'"seller"' not in l: continue
        try: m = json.loads(l)
        except ValueError: continue
        if m.get("hook") == "seller" and (m.get("ts") or 0) >= now - 86400 and m.get("decision") in ("open", "accept", "cancel", "no_stock", "reject", "declined"): dec[m["decision"]] += 1
print("[판매 24시간] 요청 " + ", ".join(f"{k} {v}" for k, v in dec.most_common()))
rev = defaultdict(float); n = 0; ours = 0.0
with open(".r3/events.jsonl", "rb") as fh:
    fh.seek(0, 2); fh.seek(max(0, fh.tell() - 40_000_000)); fh.readline()
    for l in fh:
        if b'"settle"' not in l: continue
        try: e = json.loads(l)
        except ValueError: continue
        if e.get("type") != "settle" or e.get("src") != "market" or (e.get("ts") or 0) < now - 86400: continue
        d = e.get("data") or {}; a = float(d.get("amount") or 0); n += 1
        rev[(cat.get(d.get("sku")) or {}).get("category", "?")] += a
        if d.get("own_owner") is True: ours += a
tot = sum(rev.values()) or 1
print(f"[시장 24시간] 정산 {n}건, 거래액 {f(tot)} (우리 가게 {ours/tot:.1%}), 품목군별 거래액 비중: " + ", ".join(f"{c} {v/tot:.0%}" for c, v in sorted(rev.items(), key=lambda kv: -kv[1])[:6]))
sh = json.load(open(".market/shops.json", encoding="utf-8")); print(f"[시장] 지금 목록의 가게 {len(sh)}곳")
