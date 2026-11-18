import gzip, json, re, sys, datetime as dt
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
W = sys.argv[1]
UTC = dt.timezone.utc
T0 = dt.datetime(2026,11,3,12,0,tzinfo=UTC).timestamp()
T1 = T0 + 8*3600
h = lambda t: (t - T0)/3600

# cash / stock: restock lines (no date, files in order; 11-03 starts at the 12:01 line) + policy transitions
pts = []
day = dt.datetime(2026,11,3,tzinfo=UTC).timestamp()
started = False; prev = None
for f in ["market-kit-20261103-151206.log.gz", "market-kit-20261104-020926.log.gz"]:
    for line in gzip.open(f"{W}/logs/archive/{f}", "rt", errors="replace"):
        m = re.match(r"^(\d\d):(\d\d):(\d\d) .*\[restock\].*?: cash (\d+), stock \d+ units at cost (\d+)", line)
        if not m: continue
        s = int(m[1])*3600+int(m[2])*60+int(m[3])
        if not started:
            if s < 12*3600: continue
            started = True
        if prev is not None and s < prev: day += 86400
        prev = s
        pts.append((day+s, int(m[4]), int(m[5])))
regimes = []
for line in open(f"{W}/logs/policy.log"):
    if "[shop]" not in line: continue
    ts = dt.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp()
    if ts > T1 + 3600: break
    to = re.search(r"-> (\w+) ", line)[1]
    d = json.loads(line[line.index("{"):])
    regimes.append((ts, to)); pts.append((ts, d["balance"], d["stock_cost"]))
pts = sorted(p for p in pts if T0 - 60 <= p[0] <= T1)

# deals
deals = []
for line in open(f"{W}/seller_r3_log.jsonl"):
    if '"deal"' not in line: continue
    d = json.loads(line)
    if d.get("event") == "deal" and T0 <= d["t"] < T1 and d.get("price"):
        deals.append((d["t"], d["price"]))
B = 1800
nb = int((T1-T0)//B)
cnt = [0]*nb; prices = [[] for _ in range(nb)]
for t,p in deals:
    i = int((t-T0)//B); cnt[i]+=1; prices[i].append(p)
med = [sorted(v)[len(v)//2]/1e4 if v else None for v in prices]
print("deals", len(deals), "first30", cnt[0], "points", len(pts), "median all", sorted(p for _,p in deals)[len(deals)//2])

# style
fp = [f for f in fm.findSystemFonts() if "UnDotum" in f or "Baekmuk" in f]
if fp:
    fm.fontManager.addfont(fp[0]); plt.rcParams["font.family"] = fm.FontProperties(fname=fp[0]).get_name()
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
S1, S2 = "#2a78d6", "#eb6834"
plt.rcParams.update({"axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False, "font.size": 10})
fig, ax = plt.subplots(3, 1, figsize=(9, 8.2), sharex=True, gridspec_kw={"height_ratios": [2.2, 1, 1]}, facecolor=SURF)
for a in ax:
    a.set_facecolor(SURF); a.grid(axis="y", color=GRID, lw=0.8); a.set_axisbelow(True)

# regime bands
spans = []
for i,(ts,to) in enumerate(regimes):
    end = regimes[i+1][0] if i+1 < len(regimes) else T1
    spans.append((max(ts,T0), min(end,T1), to))
for a in ax:
    for s,e,to in spans:
        if to == "FLUSH": a.axvspan(h(s), h(e), color="#eda100", alpha=0.13, lw=0)
        if to == "LAUNCH": a.axvspan(h(s), h(e), color="#8a8984", alpha=0.12, lw=0)

x = [h(p[0]) for p in pts]
ax[0].plot(x, [p[1]/1e6 for p in pts], color=S1, lw=2, label="현금(잔고)")
ax[0].plot(x, [p[2]/1e6 for p in pts], color=S2, lw=2, label="재고 원가")
ax[0].axhline(8, color=INK2, lw=1, ls=(0,(4,3)))
ax[0].text(0.7, 8.15, "FLUSH 기준 잔고 800만 원", ha="left", va="bottom", color=INK2, fontsize=9)
ax[0].set_ylabel("백만 원")
ax[0].text(h(regimes[0][0])+0.04, 0.4, "LAUNCH", color=INK2, fontsize=9)
f0 = [s for s,e,to in spans if to=="FLUSH"][0]
ax[0].annotate("4시간 20분: 잔고 818만 원 → FLUSH", xy=(h(f0), 8.18), xytext=(1.2, 10.6), color=INK, fontsize=9,
               arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
ax[0].legend(loc="lower right", frameon=False)
ax[0].set_ylim(0, max(p[1] for p in pts)/1e6*1.1)
ax[0].set_title("개장 첫 8시간 (11-03 12:00~20:00 UTC): 현금·재고와 상황 전환", loc="left", color=INK, fontsize=12, pad=10)

xb = [i*B/3600 + B/7200 for i in range(nb)]
ax[1].bar(xb, cnt, width=0.42, color=S1, edgecolor=SURF, lw=0)
ax[1].set_ylabel("판매 건수\n(30분)")
ax[1].annotate(f"첫 30분 {cnt[0]}건", xy=(xb[0], cnt[0]), xytext=(xb[0]+0.35, max(cnt)*0.92), color=INK, fontsize=9,
               arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
mx = [xb[i] for i in range(nb) if med[i] is not None]; my = [m for m in med if m is not None]
ax[2].plot(mx, my, color=S1, lw=2, marker="o", ms=4)
ax[2].set_ylabel("판매가 중앙값\n(만 원)")
ax[2].set_ylim(0, max(my)*1.25)
ax[2].set_xlabel("개장 후 시간")
ax[2].set_xticks(range(0,9)); ax[2].set_xticklabels([f"{i}h" for i in range(9)])
ax[2].set_xlim(0, 8)
from matplotlib.patches import Patch
fig.legend(handles=[Patch(color="#8a8984", alpha=0.25, label="LAUNCH"), Patch(color="#eda100", alpha=0.3, label="FLUSH (나머지는 STEADY)")],
           loc="lower right", frameon=False, ncol=2, fontsize=9, bbox_to_anchor=(0.98, 0.005))
fig.tight_layout(rect=(0, 0.03, 1, 1))
fig.savefig(f"{W}/reports/opening_day.png", dpi=150, facecolor=SURF)
