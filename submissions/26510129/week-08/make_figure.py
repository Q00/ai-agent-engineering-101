"""Write architecture.tex (TikZ) from one layout description, and draw the same layout as a preview picture.

    python make_figure.py            writes architecture.tex and architecture-preview.png
    tectonic architecture.tex        (or pdflatex / Overleaf) builds architecture.pdf from the .tex

Why a generator: the lab server has no TeX engine (installing one was refused three times), so the figure could
not be compiled while it was drawn. Every coordinate is computed here, every text is escaped and wrapped here and
checked against its box, and the same boxes are drawn with matplotlib so the layout can be looked at before the
.tex is compiled elsewhere. The preview is not the deliverable; architecture.pdf is built from architecture.tex.
"""
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
X0, W, H = -3.5, 282.5, 190.0       # millimetres: the drawing spans x from X0 to W; A4 landscape inside 5 mm margins
CHAR_MM, LINE_MM, TITLE_MM, PAD_MM, PAR_MM = 1.25, 2.81, 3.5, 1.2, 0.7

COLORS = {   # name: (fill, line)
    "env": ("FFF0D5", "B77718"), "memory": ("DDF2E4", "398052"), "code": ("DDEBFA", "2D6A9F"),
    "model": ("E9DDF7", "76519C"), "authority": ("FBE0DE", "B34B42"), "plain": ("FFFFFF", "8A96A3"),
    "gap": ("FFFFFF", "B34B42"),
}
INK = "18324A"

boxes, frames, arrows, labels = [], [], [], []


def box(x, y, w, h, style, title, *paragraphs):
    boxes.append({"x": x, "y": y, "w": w, "h": h, "style": style, "title": title, "pars": list(paragraphs)})


def frame(x, y, w, h, style, label):
    frames.append({"x": x, "y": y, "w": w, "h": h, "style": style, "label": label})


def arrow(points, style="flow"):
    arrows.append({"points": points, "style": style})


def label(x, y, text, anchor="west", rotate=0, style="memory", bold=False):
    labels.append({"x": x, "y": y, "text": text, "anchor": anchor, "rotate": rotate, "style": style, "bold": bold})


# ------------------------------------------------------------------ the layout
# Row A: the environment (top). Row B: memory, consolidated right to left. Row C: the buyer's order, left to right.
# Read counter-clockwise: an order is decided from memory (C), written to the log (B, right), consolidated into
# profiles (B, centre), reflected on at night (B, left), and the next order reads the result (C, left).
CHIP_W, CHIP_GAP = 42.0, 2.2
chip_x = [8 + i * (CHIP_W + CHIP_GAP) for i in range(6)]

frame(6, 151, 267, 21, "env", "ENVIRONMENT: the market and kit v4. What it says is data; what it pays is final")
box(chip_x[0], 153, CHIP_W, 14.5, "env", "Orders, shop lists",
    "order.json, shops.json: what to buy and who stocks it")
box(chip_x[1], 153, CHIP_W, 14.5, "env", "Shop agents (A2A)",
    "ask, stated grade, counter-offer, signed receipt: untrusted text")
box(chip_x[2], 153, CHIP_W, 14.5, "env", "Rules page (/agent.md)",
    "how a purchase is scored; it changed on 11-12 (patch 3.6)")
box(chip_x[3], 153, CHIP_W, 14.5, "env", "Public ledger",
    "every settlement: shop, SKU, price, grade")
box(chip_x[4], 153, CHIP_W, 14.5, "env", "Settlement facts",
    "deal.json, payments.jsonl, broken.jsonl")
box(chip_x[5], 153, CHIP_W, 14.5, "authority", "Kit v4 (authority)",
    "ACP checkout, AP2 mandate, accept conditions, supply")

label(6, 146.4, "MEMORY: three layers, each derived from the one to its right", bold=True)
box(8, 110, 54, 33, "memory", "PROCEDURAL: what to do",
    "objective.py: one score function; the formula's name comes from the rulebook",
    "strategy_params.json: 9 knobs inside fixed ranges, e.g. pay while a deal scores my average - 0.02",
    "lessons.jsonl: 15 lessons with their evidence, the reflection's own, alerts when the model is silent")
box(80, 110, 102, 33, "memory", "SEMANTIC: what the log adds up to (files a person can open)",
    "profiles.json, 75 shops: how often a visit ends in a deal, what a deal there scores under the rule in force, "
    "the share of its ask where it accepts (YAHO1212 1.00, psyoung82 0.83, ddolcom 0.70), warranty share, "
    "steering and bad deals",
    "my running score: 0.7305 from 16,341 remembered deals (the board: 0.7346)",
    "rulebook.json: the scoring formula as the rules page states it, and the lines that changed",
    "ledger_stats.json, category_pressure.json: what the market paid in 24 h; how my shop's categories close")
box(200, 110, 71, 33, "memory", "EPISODIC: what happened",
    "buyer_memory.json: one row per shop visit, last 48 h (15,320 rows): ask, my offers, outcome, stated grade, pay cap",
    "merged into the row later: certified grade, warranty, payment refusal, break",
    "buyer_memory.archive.jsonl: 88,151 older rows, append-only, never read on an order",
    "shop side: shop_sales.jsonl (sales the ledger shows as paid), seller_events.jsonl, restock snapshots")

# consolidation, right to left
arrow([(200, 133), (182, 133)], "data")
label(191, 134.2, "consolidate", anchor="south")
label(191, 131.8, "every order, 30 ms", anchor="north")
arrow([(200, 119), (182, 119)], "dashed")
label(191, 120.2, "rebuild when", anchor="south")
label(191, 117.8, "the rule changes", anchor="north")
box(63, 130.5, 16, 11.5, "model", "Opus", "once a night")
arrow([(80, 125), (62, 125)], "data")
label(71, 123.8, "reflect: rule,", anchor="north")
label(71, 120.9, "7 days, lessons", anchor="north")

# environment into memory
arrow([(chip_x[2] + 21, 153), (chip_x[2] + 21, 143)], "data")
label(chip_x[2] + 22, 148.2, "re-read every 30 min")
arrow([(chip_x[3] + 21, 153), (chip_x[3] + 21, 143)], "data")
label(chip_x[3] + 22, 148.2, "prices")
arrow([(chip_x[4] + 21, 153), (chip_x[4] + 21, 143)], "data")
label(chip_x[4] + 22, 148.2, "certified facts")

# Row C: the buyer
STEP_W = 42.0
step_x = [8 + i * (STEP_W + CHIP_GAP) for i in range(6)]
frame(6, 66, 267, 30, "code", "BUYER, per order: code decides from memory; a model speaks only where a shop bargains")
box(step_x[0], 68, STEP_W, 22, "code", "1 Recall",
    "the formula in force, the knobs, my running score; rows older than 48 h go to the archive")
box(step_x[1], 68, STEP_W, 22, "code", "2 Route",
    "rank shops: success, integrity, what a deal there scores against my average, warranty; "
    "skip cooled-off (30 min) and blacklisted")
box(step_x[2], 68, STEP_W, 22, "code", "3 Price the unit",
    "the shop names the grade: cost estimate, then pay cap and quick take from my average "
    "(11-17: A 0.75, B 0.64, C 0.54 of list)")
box(step_x[3], 68, STEP_W, 22, "model", "4 Bargain by shop type",
    "fixed price: pay the ask or leave; haggler: open at its threshold; unknown: probe. "
    "Haiku only if the shop moves 3% or more")
box(step_x[4], 68, STEP_W, 22, "authority", "5 Mandate guardian",
    "min(budget, pay cap), never above the ask; the budget stays out of every model context")
box(step_x[5], 68, STEP_W, 22, "code", "6 Audit, write back",
    "receipt and accept conditions before the kit pays; then the visit becomes one row")
for i in range(5):
    arrow([(step_x[i] + STEP_W, 79), (step_x[i + 1], 79)], "flow")

# memory into the order, the order into memory
arrow([(29, 110), (29, 96)], "data")
label(30, 103, "formula, knobs")
arrow([(88, 110), (88, 96)], "data")
label(89, 103, "shop profiles")
arrow([(120, 110), (120, 96)], "data")
label(121, 103, "my running score")
arrow([(164, 110), (164, 96)], "data")
label(165, 103, "acceptance threshold")
arrow([(250, 96), (250, 110)], "data")
label(249, 103, "one row per visit", anchor="east")

# environment to the order (left margin) and the order to the kit (right margin)
arrow([(8, 160), (1.5, 160), (1.5, 81), (6, 81)], "flow")
label(0.6, 120, "orders, shop lists, shop messages", anchor="south", rotate=90, style="env")
arrow([(273, 81), (277.5, 81), (277.5, 160), (271, 160)], "flow")
label(278.4, 120, "offers, accept conditions, receipts, buy plans", anchor="north", rotate=90, style="authority")

# Row D: the shop
frame(6, 31, 267, 29, "code", "SHOP, per task and per minute (code only)")
box(8, 33, 50, 20.5, "code", "Restock (every minute)",
    "categories by ledger revenue, SKUs in the price band that sells, caps on capital; "
    "on the last day only what still sells here")
box(62, 33, 50, 20.5, "code", "Price plan (per task)",
    "ledger price x arm x category pressure; floor at cost; age markdown; sell-down before the close")
box(116, 33, 50, 20.5, "authority", "Signer vault",
    "signs SKU, unit, price, the unit's true grade and its warranty; nothing else can sign")
box(170, 33, 50, 20.5, "code", "Forum note (every 5 min)",
    "a price note with ledger references, posted without being asked")
box(224, 33, 47, 20.5, "gap", "Known gap (11-17)",
    "33.4M of stock at a median age of 170 h: capital that does not turn; the seller itself was not rebuilt")

# Row E: the difference from the kit, and the clocks
box(8, 3, 142, 23, "plain", "The kit's default, and what this agent puts in its place",
    "no memory between orders -> an order log, profiles consolidated from it, and lessons",
    "the model decides every move -> code decides; Haiku where a shop moves, Opus once a night with its own history",
    "no notion of the score -> one score function whose formula comes from the rules page; a changed page is reported",
    "restock: random SKUs when stock runs low -> ledger revenue, caps on capital, a horizon rule for the last day")
box(156, 3, 115, 23, "plain", "Five clocks",
    "per turn: code, or one Haiku call (about 1,000 characters in)",
    "per order: recall and consolidate, 30 ms (it was 2.5 s of re-reading 103,368 rows)",
    "per minute: ledger, restock, price note; the rules page every 30 minutes",
    "per night: one Opus reflection (about 2,400 tokens in); a silent night is an alert",
    "on a rule change: profiles rebuilt from the archive under the new formula")

TITLE = "A market agent that remembers: an order log, profiles, lessons - and a score it reads instead of assuming"
SUBTITLE = ("Round 3 / kit v4, student 26510129 (jiseokkk). Read it counter-clockwise from the bottom left: an order is decided "
            "from memory, the visit is written to the log, the log is consolidated into profiles, a nightly reflection turns "
            "them into lessons and knob values, and the next order reads those.")
LEGEND = [("env", "environment"), ("memory", "memory"), ("code", "code"), ("model", "model"), ("authority", "authority")]


# ------------------------------------------------------------------ shared text layout
def wrapped(b):
    """Each paragraph of a box as wrapped lines; raises when the text cannot fit the box."""
    chars = int((b["w"] - 2 * PAD_MM) / CHAR_MM)
    pars = [textwrap.wrap(p, chars) for p in b["pars"]]
    need = PAD_MM + TITLE_MM + sum(len(p) for p in pars) * LINE_MM + max(0, len(pars) - 1) * PAR_MM + PAD_MM * 0.6
    if need > b["h"] + 1e-6:
        raise ValueError(f"box {b['title']!r}: needs {need:.1f} mm, has {b['h']} mm")
    if len(b["title"]) * 1.45 > b["w"] - 2 * PAD_MM:
        raise ValueError(f"box title {b['title']!r} is wider than its box")
    return pars


# ------------------------------------------------------------------ TikZ
ESCAPES = {"\\": r"\textbackslash{}", "_": r"\_", "%": r"\%", "&": r"\&", "#": r"\#", "$": r"\$", "{": r"\{", "}": r"\}",
           "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex(text):
    out = "".join(ESCAPES.get(ch, ch) for ch in text)
    assert all(ord(ch) < 128 for ch in out), f"non-ASCII text: {text!r}"
    return out.replace("->", r"$\rightarrow$")


def write_tex(path):
    lines = [
        r"% Generated by make_figure.py - edit the layout there, not here.",
        r"% Build: tectonic architecture.tex   (or pdflatex architecture.tex, or upload to Overleaf)",
        r"\documentclass[10pt]{article}",
        r"\usepackage[a4paper,landscape,margin=5mm]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{lmodern}",
        r"\usepackage{xcolor}",
        r"\usepackage{tikz}",
        r"\pagestyle{empty}",
        r"\setlength{\parindent}{0pt}",
        rf"\definecolor{{Ink}}{{HTML}}{{{INK}}}",
    ]
    for name, (fill, line) in COLORS.items():
        lines += [rf"\definecolor{{{name}Fill}}{{HTML}}{{{fill}}}", rf"\definecolor{{{name}Line}}{{HTML}}{{{line}}}"]
    lines += [r"\tikzset{", r"  txt/.style={inner sep=0pt, outer sep=0pt, align=left, font=\sffamily\scriptsize, text=Ink},",
              r"  flow/.style={->, >=latex, line width=.6pt, draw=Ink},",
              r"  data/.style={->, >=latex, line width=.6pt, draw=memoryLine},",
              r"  dashed data/.style={->, >=latex, line width=.6pt, draw=memoryLine, dashed},"]
    for name in COLORS:
        width = ".9pt" if name == "authority" else ".5pt"
        extra = ", dashed" if name == "gap" else ""
        lines.append(rf"  {name}/.style={{draw={name}Line, fill={name}Fill, rounded corners=1.2mm, line width={width}{extra}}},")
        lines.append(rf"  {name} frame/.style={{draw={name}Line, rounded corners=2mm, line width=.9pt}},")
    lines += [r"}", r"\begin{document}", r"\begin{tikzpicture}[x=1mm,y=1mm]",
              rf"\path[use as bounding box] ({X0:g},0) rectangle ({W:g},{H:g});",
              rf"\node[txt, anchor=north west, font=\sffamily\bfseries\large] at (6,{H - 1:g}) {{{tex(TITLE)}}};",
              rf"\node[txt, anchor=north west, text width=185mm] at (6,{H - 6.7:g}) {{{tex(SUBTITLE)}}};"]
    x = 198.0
    for name, word in LEGEND:
        lines.append(rf"\draw[{name}] ({x:g},{H - 10.5:g}) rectangle ({x + 4:g},{H - 7.5:g});")
        lines.append(rf"\node[txt, anchor=west] at ({x + 5:g},{H - 9:g}) {{{tex(word)}}};")
        x += 5 + len(word) * 1.2 + 2
    for f in frames:
        lines.append(rf"\draw[{f['style']} frame] ({f['x']:g},{f['y']:g}) rectangle ({f['x'] + f['w']:g},{f['y'] + f['h']:g});")
        lines.append(rf"\node[txt, anchor=north west, font=\sffamily\bfseries\scriptsize, text={f['style']}Line] "
                     rf"at ({f['x'] + 2:g},{f['y'] + f['h'] - 1:g}) {{{tex(f['label'])}}};")
    for b in boxes:
        pars = wrapped(b)
        lines.append(rf"\draw[{b['style']}] ({b['x']:g},{b['y']:g}) rectangle ({b['x'] + b['w']:g},{b['y'] + b['h']:g});")
        body = r"\\[0.7mm] ".join(r"\\ ".join(tex(line) for line in par) for par in pars)
        text = rf"{{\footnotesize\bfseries {tex(b['title'])}}}" + (r"\\ " + body if body else "")
        lines.append(rf"\node[txt, anchor=north west, text width={b['w'] - 2 * PAD_MM + 2:g}mm] "
                     rf"at ({b['x'] + PAD_MM:g},{b['y'] + b['h'] - PAD_MM:g}) {{{text}}};")
    for a in arrows:
        style = {"flow": "flow", "data": "data", "dashed": "dashed data"}[a["style"]]
        lines.append(rf"\draw[{style}] " + " -- ".join(f"({x:g},{y:g})" for x, y in a["points"]) + ";")
    for lab in labels:
        font = r"\sffamily\bfseries\footnotesize" if lab["bold"] else r"\sffamily\tiny"
        rotate = f", rotate={lab['rotate']}" if lab["rotate"] else ""
        lines.append(rf"\node[txt, anchor={lab['anchor']}, font={font}, text={lab['style']}Line{rotate}] "
                     rf"at ({lab['x']:g},{lab['y']:g}) {{{tex(lab['text'])}}};")
    lines += [r"\end{tikzpicture}", r"\end{document}", ""]
    text = "\n".join(lines)
    assert text.count("{") == text.count("}"), "unbalanced braces"
    Path(path).write_text(text, encoding="utf-8")
    return len(lines)


# ------------------------------------------------------------------ preview (matplotlib; not the deliverable)
def write_preview(path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    fig = plt.figure(figsize=((W - X0) / 25.4, H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(X0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    hexc = lambda h: "#" + h
    body_pt, title_pt = 5.4, 6.4
    spacing = LINE_MM / (body_pt * 0.3528)

    def rect(x, y, w, h, style, fill=True, lw=0.5, dashed=False, r=1.2):
        ax.add_patch(FancyBboxPatch((x + r, y + r), w - 2 * r, h - 2 * r, boxstyle=f"round,pad={r}",
                                    fc=hexc(COLORS[style][0]) if fill else "none", ec=hexc(COLORS[style][1]),
                                    lw=lw, ls="--" if dashed else "-"))

    ax.text(6, H - 1, TITLE, fontsize=9.3, fontweight="bold", color=hexc(INK), va="top", ha="left")
    ax.text(6, H - 6.7, "\n".join(textwrap.wrap(SUBTITLE, 148)), fontsize=body_pt, color=hexc(INK), va="top", ha="left",
            linespacing=spacing)
    x = 198.0
    for name, word in LEGEND:
        rect(x, H - 10.5, 4, 3, name, r=0.5)
        ax.text(x + 5, H - 9, word, fontsize=body_pt, color=hexc(INK), va="center", ha="left")
        x += 5 + len(word) * 1.2 + 2
    for f in frames:
        rect(f["x"], f["y"], f["w"], f["h"], f["style"], fill=False, lw=0.9, r=2)
        ax.text(f["x"] + 2, f["y"] + f["h"] - 1, f["label"], fontsize=body_pt, fontweight="bold",
                color=hexc(COLORS[f["style"]][1]), va="top", ha="left")
    for b in boxes:
        rect(b["x"], b["y"], b["w"], b["h"], b["style"], lw=0.9 if b["style"] == "authority" else 0.5, dashed=b["style"] == "gap")
        top = b["y"] + b["h"] - PAD_MM
        ax.text(b["x"] + PAD_MM, top, b["title"], fontsize=title_pt, fontweight="bold", color=hexc(INK), va="top", ha="left")
        y = top - TITLE_MM
        for par in wrapped(b):
            ax.text(b["x"] + PAD_MM, y, "\n".join(par).replace("->", "→"), fontsize=body_pt, color=hexc(INK),
                    va="top", ha="left", linespacing=spacing)
            y -= len(par) * LINE_MM + PAR_MM
    for a in arrows:
        color = hexc(INK) if a["style"] == "flow" else hexc(COLORS["memory"][1])
        pts = a["points"]
        for (x1, y1), (x2, y2) in zip(pts[:-2], pts[1:-1]):
            ax.plot([x1, x2], [y1, y2], color=color, lw=0.7, ls="--" if a["style"] == "dashed" else "-")
        ax.annotate("", xy=pts[-1], xytext=pts[-2],
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=0.7, ls="--" if a["style"] == "dashed" else "-",
                                    shrinkA=0, shrinkB=0, mutation_scale=6))
    align = {"west": ("left", "center"), "east": ("right", "center"), "south": ("center", "bottom"), "north": ("center", "top")}
    for lab in labels:
        ha, va = align[lab["anchor"]]
        if lab["rotate"]:   # a rotated label's anchor is along the rotated axis
            ha, va = ("right", "center") if lab["anchor"] == "south" else ("left", "center")
        ax.text(lab["x"], lab["y"], lab["text"], fontsize=title_pt if lab["bold"] else 4.4,
                fontweight="bold" if lab["bold"] else "normal", color=hexc(COLORS[lab["style"]][1]), ha=ha, va=va,
                rotation=lab["rotate"], rotation_mode="anchor")
    fig.savefig(path, dpi=220)
    plt.close(fig)


if __name__ == "__main__":
    n = write_tex(HERE / "architecture.tex")
    print(f"architecture.tex: {n} lines, {len(boxes)} boxes, {len(arrows)} arrows, {len(labels)} labels")
    try:
        write_preview(HERE / "architecture-preview.png")
        print("architecture-preview.png written (a matplotlib drawing of the same layout; not built from the .tex)")
    except ImportError:
        print("matplotlib is not installed here: no preview")
