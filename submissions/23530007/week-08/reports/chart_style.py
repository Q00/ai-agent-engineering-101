"""Shared look for the REPORT charts: one accent for our shop (orange), one helper hue (aqua), gray for everyone else.
Orange/aqua/yellow pass the dataviz palette checks (CVD and normal-vision separation); aqua and yellow sit under
3:1 against the surface, so every mark in those colors carries a text label."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

US = "#d95926"          # our shop
US_RAMP = ["#f6c7ae", "#ec8a5d", "#a8401a"]   # light -> dark orange, for ordered bands (cheap -> dear)
AQUA = "#1baf7a"        # second series
YELLOW = "#eda100"      # regime / period highlight (always labelled)
OTHER = "#cfcec8"       # other shops
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
W, H = 6.4, 3.6         # inches; saved at 130 dpi -> about 830 px wide
DPI = 130


def setup():
    fp = [f for f in fm.findSystemFonts() if "UnDotum" in f]
    if fp:
        fm.fontManager.addfont(fp[0])
        plt.rcParams["font.family"] = fm.FontProperties(fname=fp[0]).get_name()
    plt.rcParams.update({"axes.edgecolor": GRID, "xtick.color": INK2, "ytick.color": INK2, "font.size": 9,
                         "axes.labelcolor": INK2, "axes.spines.top": False, "axes.spines.right": False})


def fig(w=W, h=H, **kw):
    setup()
    f, ax = plt.subplots(figsize=(w, h), facecolor=SURF, **kw)
    for a in (ax if hasattr(ax, "__iter__") else [ax]):
        a.set_facecolor(SURF)
    return f, ax


def title(ax, text):
    ax.set_title(text, loc="left", color=INK, fontsize=11, pad=8)


def save(f, out):
    f.savefig(out, dpi=DPI, facecolor=SURF)
