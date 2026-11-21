"""Figures for REPORT.md: figures/rank_profit.svg and figures/refusals.svg.

Usage: python report_figures.py <trend.txt> <out_dir>
trend.txt lines: "MM-DD HH:MM|rank of pct total|balance ... profit P ... deals D ... stars S broken B ..."
made from the hourly monitor logs (run in logs/):
  for f in monitor-*.txt; do h=$(head -1 $f | sed -E 's/== ([0-9-]+ [0-9:]+) KST.*/\\1/');
    c=$(grep -m1 'CLASS rank' $f | sed -E 's/.*CLASS rank ([0-9]+) of ([0-9]+), pct ([0-9.]+), total ([0-9.]+).*/\\1 \\2 \\3 \\4/');
    p=$(grep -m1 '^balance' $f); echo "$h|$c|$p"; done > trend.txt
Lines with no CLASS rank (checks before 11-06 10:33) are skipped.
"""
import os, re, sys
from datetime import datetime

trend_path, out_dir = sys.argv[1:3]

# ---------- data from the hourly monitor logs ----------
pts = []
for line in open(trend_path, encoding="utf-8"):
    t, c, p = line.rstrip("\n").split("|")
    if not c.strip():
        continue
    rank, of, pct, total = c.split()
    m = re.search(r"profit ([0-9,-]+).*deals (\d+).*stars ([0-9.]+)\s+broken (\d+)", p)
    ts = datetime.strptime("2026-" + t, "%Y-%m-%d %H:%M")
    pts.append(dict(ts=ts, rank=int(rank), of=int(of), total=float(total),
                    profit=int(m.group(1).replace(",", "")), deals=int(m.group(2)),
                    stars=float(m.group(3)), broken=int(m.group(4))))

T0 = datetime(2026, 11, 6, 0, 0)
T1 = datetime(2026, 11, 18, 0, 0)


def X(ts, x0, w):
    return x0 + (ts - T0).total_seconds() / (T1 - T0).total_seconds() * w


EVENTS = [  # (KST time, label, commit)
    (datetime(2026, 11, 6, 9, 26), "구매 첫 제안 55%", "682b169"),
    (datetime(2026, 11, 12, 10, 44), "promo 시작", "a6922db"),
    (datetime(2026, 11, 12, 19, 34), "등급별 구매 상한", "26635fd"),
    (datetime(2026, 11, 13, 18, 21), "식품 6/10", "ec777ed"),
    (datetime(2026, 11, 14, 19, 3), "고가 사입 문 열기", "ac80d88"),
    (datetime(2026, 11, 16, 0, 0), "고가 사입 중지", "HIGH_STOP_TS"),
    (datetime(2026, 11, 17, 12, 0), "전체 사입 중지", "ALL_STOP_TS"),
]


def trend_svg():
    W, H = 880, 430
    x0, w = 64, 780
    # panel 1: class rank (top), panel 2: profit in 억 (bottom)
    r_top, r_h = 46, 150
    p_top, p_h = 262, 120
    rmin, rmax = 8, 26
    pmax = 4.5e8

    def YR(r):
        return r_top + (r - rmin) / (rmax - rmin) * r_h

    def YP(v):
        return p_top + p_h - v / pmax * p_h

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" role="img" '
         'aria-label="11-06부터 11-17까지 1시간 점검마다 기록한 학생 Class 순위와 가게 이익">',
         '<title>학생 Class 순위와 가게 이익의 시간 추이</title>', f'<rect width="{W}" height="{H}" fill="#fff"/>',
         '<style>.ax{stroke:#d8dfe5;stroke-width:1}.tx{fill:#596674;font-size:11px}'
         '.lb{fill:#202832;font-size:12px;font-weight:600}.ev{stroke:#236b70;stroke-width:1;stroke-dasharray:3 3;opacity:.7}'
         '.evt{fill:#236b70;font-size:10.5px}.ln{fill:none;stroke:#202832;stroke-width:1.8;stroke-linejoin:round}'
         '.lp{fill:none;stroke:#236b70;stroke-width:1.8;stroke-linejoin:round}.dot{fill:#236b70}</style>']
    # day grid
    for d in range(6, 19):
        ts = datetime(2026, 11, d)
        x = X(ts, x0, w)
        s.append(f'<line class="ax" x1="{x:.1f}" y1="{r_top}" x2="{x:.1f}" y2="{p_top + p_h}"/>')
        if d < 18:
            s.append(f'<text class="tx" x="{X(datetime(2026, 11, d, 12), x0, w):.1f}" y="{p_top + p_h + 18}" text-anchor="middle">11-{d:02d}</text>')
    # rank axis
    s.append(f'<text class="lb" x="{x0}" y="{r_top - 26}">학생 Class 순위 (46명 안팎, 위가 좋음)</text>')
    for r in (10, 15, 20, 25):
        y = YR(r)
        s.append(f'<line class="ax" x1="{x0}" y1="{y:.1f}" x2="{x0 + w}" y2="{y:.1f}" opacity=".6"/>')
        s.append(f'<text class="tx" x="{x0 - 8}" y="{y + 4:.1f}" text-anchor="end">{r}위</text>')
    path = " ".join(f'{"M" if i == 0 else "L"}{X(p["ts"], x0, w):.1f},{YR(p["rank"]):.1f}' for i, p in enumerate(pts))
    s.append(f'<path class="ln" d="{path}"/>')
    first, last = pts[0], pts[-1]
    s.append(f'<circle class="dot" cx="{X(last["ts"], x0, w):.1f}" cy="{YR(last["rank"]):.1f}" r="4"/>')
    s.append(f'<text class="lb" x="{X(last["ts"], x0, w) - 6:.1f}" y="{YR(last["rank"]) - 9:.1f}" text-anchor="end">10위</text>')
    s.append(f'<text class="tx" x="{X(first["ts"], x0, w) + 6:.1f}" y="{YR(first["rank"]) + 4:.1f}">{first["rank"]}위 (첫 Class 기록)</text>')
    # profit axis
    s.append(f'<text class="lb" x="{x0}" y="{p_top - 12}">가게 이익 (억 원)</text>')
    for v in (0, 1e8, 2e8, 3e8, 4e8):
        y = YP(v)
        s.append(f'<line class="ax" x1="{x0}" y1="{y:.1f}" x2="{x0 + w}" y2="{y:.1f}" opacity=".6"/>')
        s.append(f'<text class="tx" x="{x0 - 8}" y="{y + 4:.1f}" text-anchor="end">{int(v / 1e8)}</text>')
    path = " ".join(f'{"M" if i == 0 else "L"}{X(p["ts"], x0, w):.1f},{YP(p["profit"]):.1f}' for i, p in enumerate(pts))
    s.append(f'<path class="lp" d="{path}"/>')
    # events
    for i, (ts, label, _) in enumerate(EVENTS):
        x = X(ts, x0, w)
        s.append(f'<line class="ev" x1="{x:.1f}" y1="{r_top - 4}" x2="{x:.1f}" y2="{p_top + p_h}"/>')
        ty = r_top + r_h + 22 + (i % 3) * 13
        if x > x0 + w - 100:
            s.append(f'<text class="evt" x="{x - 3:.1f}" y="{ty}" text-anchor="end">{label}</text>')
        else:
            s.append(f'<text class="evt" x="{x + 3:.1f}" y="{ty}">{label}</text>')
    s.append('</svg>')
    return "".join(s)


REFUSALS = [
    ("재고 아닌 단위로 checkout", 198), ("유통기한 미달로 판매 거절", 112), ("시장 결제 오류 402", 7),
    ("상대 가게 checkout 500", 3), ("시장 settle 500", 2), ("연결 끊김", 2), ("정산 조건 not_settled_before", 1)]


def refusal_svg():
    W, row = 880, 30
    H = 40 + row * len(REFUSALS) + 10
    x0, w = 250, 560
    mx = max(v for _, v in REFUSALS)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" role="img" '
         'aria-label="마지막 두 로그 파일의 거부·실패 사유별 줄 수">',
         '<title>거부·실패 사유 (줄 수)</title>', f'<rect width="{W}" height="{H}" fill="#fff"/>',
         '<style>.tx{fill:#202832;font-size:12.5px}.nv{fill:#596674;font-size:12px}.b1{fill:#236b70}.b2{fill:#596674;opacity:.55}</style>']
    for i, (k, v) in enumerate(REFUSALS):
        y = 26 + i * row
        bw = max(2, v / mx * w)
        cls = "b1" if i < 2 else "b2"
        s.append(f'<text class="tx" x="{x0 - 10}" y="{y + 15}" text-anchor="end">{k}</text>')
        s.append(f'<rect class="{cls}" x="{x0}" y="{y + 3}" width="{bw:.1f}" height="17" rx="2"/>')
        s.append(f'<text class="nv" x="{x0 + bw + 8:.1f}" y="{y + 16}">{v}</text>')
    s.append('</svg>')
    return "".join(s)



os.makedirs(out_dir, exist_ok=True)
for name, svg in (("rank_profit.svg", trend_svg()), ("refusals.svg", refusal_svg())):
    open(os.path.join(out_dir, name), "w", encoding="utf-8").write(svg + "\n")
print(len(pts), "points; wrote", out_dir)
