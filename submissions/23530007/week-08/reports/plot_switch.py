"""REPORT 3.2 chart: revenue per deal by day, our shop vs the market average (public ledger, 11-03 12:00 ~ 11-08).
Run: python3 reports/plot_switch.py data/market_archive.db reports/switch.png"""
import sqlite3, json, sys, collections, datetime as dt
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
db, out = sys.argv[1], sys.argv[2]
UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 3, 12, tzinfo=UTC).timestamp(); T1 = dt.datetime(2026, 11, 9, tzinfo=UTC).timestamp()
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
u2n = {}
for (d,) in c.execute("select data from ledger where kind='join' and ts<?", (T1,)):
    x = json.loads(d)["data"]
    if x.get("role") == "shop": u2n[x["url"]] = x["name"]
rev = collections.defaultdict(collections.Counter); n = collections.defaultdict(collections.Counter)
for ts, d in c.execute("select ts,data from ledger where round=3 and kind='settle' and ts>=? and ts<?", (T0, T1)):
    x = json.loads(d)["data"]; s = u2n.get(x["shop"], x["shop"])
    day = dt.datetime.fromtimestamp(ts, UTC).strftime("%m-%d")
    rev[day][s] += x["amount"]; n[day][s] += 1
days = sorted(rev); ME = "yms979"
us = [rev[d][ME]/n[d][ME]/1e4 for d in days]
mkt = [sum(rev[d].values())/sum(n[d].values())/1e4 for d in days]
rank = [sorted(rev[d], key=lambda k: -rev[d][k]).index(ME) + 1 for d in days]
print(list(zip(days, [round(u, 1) for u in us], [round(m, 1) for m in mkt], rank)))

fp = [f for f in fm.findSystemFonts() if "UnDotum" in f]
if fp: fm.fontManager.addfont(fp[0]); plt.rcParams["font.family"] = fm.FontProperties(fname=fp[0]).get_name()
INK, INK2, GRID, SURF, ME_C, MKT = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb", "#2a78d6", "#a3a29c"
plt.rcParams.update({"axes.edgecolor": GRID, "xtick.color": INK2, "ytick.color": INK2, "font.size": 11,
                     "axes.spines.top": False, "axes.spines.right": False})
fig, ax = plt.subplots(figsize=(8.5, 4.6), facecolor=SURF); ax.set_facecolor(SURF)
ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
x = range(len(days))
ax.plot(x, mkt, color=MKT, lw=2, marker="o", ms=6)
ax.plot(x, us, color=ME_C, lw=2.6, marker="o", ms=7)
ax.text(len(days) - 1 + 0.12, us[-1], "우리", color=INK, va="center", fontweight="bold")
ax.text(len(days) - 1 + 0.12, mkt[-1], "시장 평균", color=INK2, va="center")
i5 = days.index("11-05")
ax.annotate(f"{us[i5]:.1f}만 원, 시장 평균의 {us[i5]/mkt[i5]:.1f}배\n그날 매출 시장 1위", xy=(i5, us[i5]), xytext=(i5 + 0.45, max(us) * 1.1),
            color=INK, fontsize=10, va="top", arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
ax.annotate(f"{us[0]:.1f}만 원 (시장 {mkt[0]:.1f}만)", xy=(0, us[0]), xytext=(0.3, 1.5), color=INK2, fontsize=10,
            arrowprops=dict(arrowstyle="-", color=INK2, lw=0.8))
ax.set_xticks(list(x)); ax.set_xticklabels(days)
ax.set_xlim(-0.2, len(days) - 1 + 0.2); ax.set_ylim(0, max(us) * 1.12)
ax.set_ylabel("거래당 매출 (만 원)", color=INK2)
ax.set_title("비싼 물건으로 전환한 뒤 거래당 매출이 시장 평균을 넘었다", loc="left", color=INK, fontsize=13, pad=10)
fig.tight_layout(); fig.subplots_adjust(right=0.86)
fig.savefig(out, dpi=150, facecolor=SURF)
