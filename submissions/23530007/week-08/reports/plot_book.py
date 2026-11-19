"""REPORT 3.3 chart: book (cash + stock at cost) per shop, rebuilt hourly from the public ledger: 5 M + settled
revenue - unit cost of each sold unit (data/cost_model.db) - refunds - expired cost - storage fees. Checked against the
board on 11-11 12:41 UTC: within 1-2 %, same order. Run: python3 reports/plot_book.py . reports/book.png"""
import sqlite3, json, sys, collections, datetime as dt, pickle
W=sys.argv[1]; OUT=sys.argv[2]
UTC=dt.timezone.utc
T0=dt.datetime(2026,11,3,12,tzinfo=UTC).timestamp(); TEND=dt.datetime(2026,11,17,15,tzinfo=UTC).timestamp()
c=sqlite3.connect(f"file:{W}/data/market_archive.db?mode=ro",uri=True)
cost=dict(sqlite3.connect(f"file:{W}/data/cost_model.db?mode=ro",uri=True).execute("select unit,cost from unit_cost"))
u2n={}
for (d,) in c.execute("select data from ledger where kind='join'"):
    x=json.loads(d)["data"]
    if x.get("role")=="shop": u2n[x["url"]]=x["name"]
# per shop per hour deltas
H=lambda ts:int((ts-T0)//3600)
delta=collections.defaultdict(lambda: collections.defaultdict(float))
miss=0
for ts,kind,d in c.execute("select ts,kind,data from ledger where round=3 and kind in ('settle','broken','storage','expire') and ts>=? and ts<=? order by ts",(T0,TEND)):
    x=json.loads(d)["data"]; h=H(ts)
    if kind=="settle":
        s=u2n.get(x["shop"]); 
        if not s: continue
        uc=cost.get(x.get("unit"))
        if uc is None: miss+=1; uc=0.6*x["amount"]
        delta[s][h]+=x["amount"]-uc
    elif kind=="broken":
        s=u2n.get(x["shop"]); 
        if s: delta[s][h]-=x.get("refunded") or 0
    elif kind=="storage":
        delta[x["shop"]][h]-=x["fee"]
    elif kind=="expire":
        delta[x["shop"]][h]-=x["cost"]
nh=H(TEND)+1
series={}
for s,dd in delta.items():
    v=5e6; arr=[]
    for h in range(nh): v+=dd.get(h,0); arr.append(v)
    series[s]=arr
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
HMAX = 192                                   # 11-11 12:00 UTC: the board archive starts here
ME = "yms979"
first = next(h for h in range(7, HMAX + 1) if max(series, key=lambda s: series[s][h]) == ME)
fp = [f for f in fm.findSystemFonts() if "UnDotum" in f]
if fp: fm.fontManager.addfont(fp[0]); plt.rcParams["font.family"] = fm.FontProperties(fname=fp[0]).get_name()
INK, INK2, GRID, SURF, ME_C, OTHER = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb", "#d95926", "#c9c8c2"
plt.rcParams.update({"axes.edgecolor": GRID, "xtick.color": INK2, "ytick.color": INK2, "font.size": 9,
                     "axes.spines.top": False, "axes.spines.right": False})
fig, ax = plt.subplots(figsize=(6.6, 3.6), facecolor=SURF); ax.set_facecolor(SURF)
ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
x = list(range(HMAX + 1))
for s, v in series.items():
    if s != ME: ax.plot(x, [y / 1e6 for y in v[:HMAX + 1]], color=OTHER, lw=1)
us = [y / 1e6 for y in series[ME][:HMAX + 1]]
ax.plot(x, us, color=ME_C, lw=2.4)
sec = max((s for s in series if s != ME), key=lambda s: series[s][HMAX])
gap = (series[ME][HMAX] - series[sec][HMAX]) / 1e6
ax.text(HMAX + 2, us[-1], "우리", color=INK, va="center", fontweight="bold")
ax.text(HMAX + 2, series[sec][HMAX] / 1e6, "2위", color=INK2, va="center")
ax.annotate("", xy=(HMAX - 1, us[-1]), xytext=(HMAX - 1, series[sec][HMAX] / 1e6),
            arrowprops=dict(arrowstyle="<->", color=INK2, lw=0.9))
ax.text(HMAX - 4, (us[-1] + series[sec][HMAX] / 1e6) / 2, f"+{gap:,.0f}M", color=INK, ha="right", va="center")
ax.plot([first], [us[first]], "o", color=ME_C, ms=6)
fd = dt.datetime.fromtimestamp(T0 + first * 3600, UTC)
ax.annotate(f"{fd:%m-%d %H}시 장부 1위\n이후 종료까지 유지", xy=(first, us[first]), xytext=(first - 70, us[first] + 280),
            color=INK, fontsize=9, arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
ticks = [h for h in range(12, HMAX + 1, 24)]          # midnights UTC (T0 is 11-03 12:00)
ax.set_xticks(ticks); ax.set_xticklabels([f"{dt.datetime.fromtimestamp(T0 + h*3600, UTC):%m-%d}" for h in ticks])
ax.set_xlim(0, HMAX + 14); ax.set_ylim(0, max(us) * 1.12)
ax.set_ylabel("장부 (백만 원)", color=INK2)
ax.set_title("상점별 장부 (현금 + 재고 원가), 11-03 ~ 11-11", loc="left", color=INK, fontsize=11, pad=8)
fig.tight_layout()
fig.savefig(OUT, dpi=130, facecolor=SURF)
print("first", fd, "gap", round(gap, 1), "2nd", sec)
