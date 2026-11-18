"""Opening-day chart for REPORT 3.1: cumulative deals per shop in the first 12 h of Round 3 (public ledger).
Run: python3 reports/plot_opening_day.py data/market_archive.db reports/opening_day.png"""
import sqlite3, json, sys, collections, datetime as dt
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
db, out = sys.argv[1], sys.argv[2]
T0 = dt.datetime(2026, 11, 3, 12, tzinfo=dt.timezone.utc).timestamp(); H = 12; T1 = T0 + H*3600
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
u2n = {}
for (d,) in c.execute("select data from ledger where kind='join' and ts<?", (T1,)):
    x = json.loads(d)["data"]
    if x.get("role") == "shop": u2n[x["url"]] = x["name"]
ts_by = collections.defaultdict(list)
for ts, d in c.execute("select ts,data from ledger where round=3 and kind='settle' and ts>=? and ts<?", (T0, T1)):
    x = json.loads(d)["data"]; ts_by[u2n.get(x["shop"], x["shop"])].append(ts)
ME = "yms979"
total = sum(len(v) for v in ts_by.values())
fp = [f for f in fm.findSystemFonts() if "UnDotum" in f]
if fp: fm.fontManager.addfont(fp[0]); plt.rcParams["font.family"] = fm.FontProperties(fname=fp[0]).get_name()
INK, INK2, GRID, SURF, ME_C, OTHER = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb", "#2a78d6", "#c9c8c2"
plt.rcParams.update({"axes.edgecolor": GRID, "xtick.color": INK2, "ytick.color": INK2, "font.size": 11,
                     "axes.spines.top": False, "axes.spines.right": False})
fig, ax = plt.subplots(figsize=(8.5, 4.6), facecolor=SURF); ax.set_facecolor(SURF)
ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
def line(v): v = sorted(v); return [0] + [(t - T0)/3600 for t in v] + [H], [0] + list(range(1, len(v)+1)) + [len(v)]
ranked = sorted(ts_by, key=lambda s: -len(ts_by[s]))
for s in ranked:
    if s == ME: continue
    x, y = line(ts_by[s]); ax.step(x, y, where="post", color=OTHER, lw=1.2)
x, y = line(ts_by[ME]); ax.step(x, y, where="post", color=ME_C, lw=2.6)
me_n = len(ts_by[ME]); second = max(len(ts_by[s]) for s in ts_by if s != ME)
ax.text(H + 0.12, me_n, f"우리 {me_n:,}건 ({100*me_n/total:.1f}%)", color=INK, va="center", fontsize=11, fontweight="bold")
ax.text(H + 0.12, second, f"2위 {second:,}건", color=INK2, va="center", fontsize=10)
ax.text(H + 0.12, second*0.45, f"다른 상점 {len(ts_by)-1}곳", color=INK2, va="center", fontsize=10)
ax.set_xlim(0, H); ax.set_ylim(0, me_n*1.08)
ax.set_xticks(range(0, H+1, 2)); ax.set_xticklabels([f"{i}h" for i in range(0, H+1, 2)])
ax.set_xlabel("개장 후 시간 (11-03 12:00 UTC부터)", color=INK2)
ax.set_title("개장 12시간 누적 판매 건수: 시장 1위", loc="left", color=INK, fontsize=13, pad=10)
fig.tight_layout(); fig.subplots_adjust(right=0.80)
fig.savefig(out, dpi=150, facecolor=SURF)
print(me_n, second, total, len(ts_by))
