"""REPORT 3.2 chart: our settled revenue by price band, before the switch (11-03 12:00-24:00) and after it (11-05),
from the public ledger. Run: python3 reports/plot_switch.py data/market_archive.db reports/switch.png"""
import sqlite3, json, sys, collections, datetime as dt
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
db, out = sys.argv[1], sys.argv[2]
UTC = dt.timezone.utc
T = lambda d, h=0: dt.datetime(2026, 11, d, h, tzinfo=UTC).timestamp()
WIN = [("전환 전: 11-03 (개장 12시간)", T(3, 12), T(4)), ("전환 후: 11-05", T(5), T(6))]
BANDS = [(0, 1e5, "10만 원 미만"), (1e5, 3e5, "10~30만 원"), (3e5, 1e13, "30만 원 이상")]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
ours = set()
for (d,) in c.execute("select data from ledger where kind='join' and ts<?", (T(6),)):
    x = json.loads(d)["data"]
    if x.get("role") == "shop" and x["name"] == "yms979": ours.add(x["url"])
res = []
for title, a, b in WIN:
    rev = collections.Counter(); n = 0
    for (d,) in c.execute("select data from ledger where round=3 and kind='settle' and ts>=? and ts<?", (a, b)):
        x = json.loads(d)["data"]
        if x["shop"] not in ours: continue
        n += 1
        for lo, hi, nm in BANDS:
            if lo <= x["amount"] < hi: rev[nm] += x["amount"]
    res.append((title, [rev[nm] for _, _, nm in BANDS], n))
    print(title, n, {nm: round(rev[nm] / 1e6, 1) for _, _, nm in BANDS})

fp = [f for f in fm.findSystemFonts() if "UnDotum" in f]
if fp: fm.fontManager.addfont(fp[0]); plt.rcParams["font.family"] = fm.FontProperties(fname=fp[0]).get_name()
INK, INK2, SURF = "#0b0b0b", "#52514e", "#fcfcfb"
COLORS = ["#b7d3f6", "#5598e7", "#184f95"]          # one hue, light -> dark = cheap -> dear
plt.rcParams.update({"font.size": 9})
fig, axes = plt.subplots(1, 2, figsize=(6.4, 3.7), facecolor=SURF)
for ax, (title, vals, n) in zip(axes, res):
    tot = sum(vals)
    w, _ = ax.pie(vals, colors=COLORS, startangle=90, counterclock=False,
                  wedgeprops=dict(width=0.42, edgecolor=SURF, linewidth=2))
    for k, (wedge, v) in enumerate(zip(w, vals)):
        share = v / tot
        if share < 0.05 or k == 2: continue   # the dear share is written in the centre
        ang = (wedge.theta1 + wedge.theta2) / 2
        import math
        r = 0.79
        ax.text(r * math.cos(math.radians(ang)), r * math.sin(math.radians(ang)), f"{share:.0%}",
                ha="center", va="center", fontsize=10, color="#ffffff" if wedge.get_facecolor()[0] < 0.5 else INK, fontweight="bold")
    dear = vals[2] / tot
    ax.text(0, 0.1, f"{dear:.0%}", ha="center", va="center", fontsize=17, color=INK, fontweight="bold")
    ax.text(0, -0.22, "30만 원 이상", ha="center", va="center", fontsize=8.5, color=INK2)
    ax.set_title(f"{title}\n매출 {tot/1e6:,.0f}M · 판매 {n:,}건", color=INK, fontsize=10, pad=4)
from matplotlib.patches import Patch
fig.legend(handles=[Patch(color=col, label=nm) for col, (_, _, nm) in zip(COLORS, BANDS)], loc="lower center",
           ncol=3, frameon=False, fontsize=8.5, title="판매가 구간별 매출 비중", title_fontsize=8.5)
fig.subplots_adjust(left=0.03, right=0.97, top=0.84, bottom=0.18, wspace=0.12)
fig.savefig(out, dpi=130, facecolor=SURF)
