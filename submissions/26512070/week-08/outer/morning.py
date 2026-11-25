"""The night in one report for the morning (user 11/5 02:0x: change the strategy every 30 min, and at night gather it
all and report it in the morning).

    py -m outer.morning [YYYY-MM-DD]     # the night 00:00-08:00 of that day (default today)

No model: what the night's slices recommended and installed (logs/strategy/briefs, strategy history), the alerts,
and how our ranks moved from the first kept board of the night to the last (outer/position.py). Written to
logs/strategy/morning-<YYYYMMDD>.md (Korean, for the user). outer/board_history.py writes it once after 08:00.
"""
import json
import re
import sys
import time

from core import atomic, paths
from outer import position

START_H, END_H = 0, 8


def _flat(d, pre=""):
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            out.update(_flat(v, pre + k + "."))
        else:
            out[pre + k] = v
    return out


def diff(a, b):
    """Keys that changed from strategy a to b (not version, epoch, approved)."""
    fa, fb = _flat(a), _flat(b)
    skip = ("version", "epoch", "approved")
    return {k: (fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb))
            if fa.get(k) != fb.get(k) and k not in skip}


def _short(v, n=60):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n - 3] + "..."


def build(day=None):
    day = day or time.strftime("%Y-%m-%d")
    t0 = time.mktime(time.strptime(f"{day} {START_H:02d}:00", "%Y-%m-%d %H:%M"))
    t1 = t0 + (END_H - START_H) * 3600
    hist = paths.memory() / "strategy" / "history"
    L = [f"# 밤 보고 {day} {START_H:02d}:00-{END_H:02d}:00", ""]

    # ranks: first and last kept board of the night
    snaps = [s for s in position.snapshots(t0) if s["ts"] <= t1]
    if snaps:
        a, b = position.ranks(snaps[0]), position.ranks(snaps[-1])
        ta, tb = (time.strftime("%H:%M", time.localtime(s["ts"])) for s in (snaps[0], snaps[-1]))
        L += [f"## 순위 {ta} -> {tb}", "", "| 항목 | 값 | 등수 |", "|---|---|---|"]
        if a.get("players") and b.get("players"):
            L.append(f"| Players 합계 | {position._n(a['players']['value'])} -> {position._n(b['players']['value'])} | "
                     f"{a['players']['rank']} -> {b['players']['rank']} / {b['players']['of']} |")
        for side, name in (("shop", "가게"), ("buyer", "buyer")):
            for f, p in ((b.get(side) or {}).get("parts") or {}).items():
                q = ((a.get(side) or {}).get("parts") or {}).get(f) or {}
                L.append(f"| {name} {position.LABEL[f]} | {position._n(q.get('value'))} -> {position._n(p['value'])} | "
                         f"{q.get('rank', '-')} -> {p['rank']} / {p['of']} |")
        L.append("")
    else:
        L += ["## 순위", "", "이 시간의 순위표 이력이 없다 (outer/board_history.py).", ""]

    # what the slices recommended and what was installed
    L += ["## 전략 변경", "", "| 시각 | 브리핑 | 추천 | 설치 | 바꾼 것 | 기대 |", "|---|---|---|---|---|---|"]
    n_inst = 0
    for f in sorted((paths.root() / "logs" / "strategy" / "briefs").glob("*.json")):
        d = atomic.read_json(f, default=None) or {}
        if not (t0 <= (d.get("ts") or 0) < t1):
            continue
        rec = d.get("recommendation")
        opt = next((o for o in d.get("options") or [] if o.get("id") == rec), None)
        dec = d.get("decision") or {}
        v = dec.get("installed")
        changed = ""
        if v:
            n_inst += 1
            prev = atomic.read_json(hist / f"v{d.get('version_from')}.json", default={}) or {}
            new = atomic.read_json(hist / f"v{v}.json", default={}) or {}
            changed = "; ".join(f"{k} {_short(x, 30)} -> {_short(y, 30)}" for k, (x, y) in diff(prev, new).items())
        when = time.strftime("%H:%M", time.localtime(d["ts"]))
        L.append(f"| {when} | {d.get('proposal_id')} | {rec or '없음'} | {('v' + str(v)) if v else '-'} | "
                 f"{changed or '-'} | {_short((opt or {}).get('expected', ''), 120) if opt else '-'} |")
    L += ["", f"설치 {n_inst}건.", ""]

    # alerts in the window
    urgent = paths.root() / "logs" / "strategy" / "URGENT.md"
    lines = []
    if urgent.exists():
        for line in urgent.read_text(encoding="utf-8").splitlines():
            m = re.match(r"- (\d\d)-(\d\d) (\d\d):(\d\d) ", line)
            if m:
                t = time.mktime(time.strptime(f"{day[:4]}-{m.group(1)}-{m.group(2)} {m.group(3)}:{m.group(4)}",
                                              "%Y-%m-%d %H:%M"))
                if t0 <= t < t1:
                    lines.append(line)
    L += ["## 경보", ""] + (lines or ["(없음)"]) + [""]

    out = paths.root() / "logs" / "strategy" / f"morning-{day.replace('-', '')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else None))
