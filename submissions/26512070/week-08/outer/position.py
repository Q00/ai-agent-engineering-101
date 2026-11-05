"""Where we stand, part by part (user 11/5 02:0x: 'I must see each part's live rank to know my position').

From one public board snapshot (outer/board_history.py) and the rule in core/score.py: for the Players total and each
part of shop ALL and buyer ALL - our value, our rank among the ranked rows (1 = best), percentile, what it adds to
ALL, the player just above and how far, and how our rank moved over the last 1 h and 6 h of the kept history.
Writes memory/facts/position.json and logs/strategy/position.md (Korean, for the user).

    py -m outer.position        # from the newest kept snapshot
"""
import gzip
import json
import time

from core import atomic, paths, score

ME = "gom31"
LABEL = {"book": "장부(book)", "profit": "현금(cash)", "revenue": "매출", "stars_rank": "별점", "surplus": "잉여", "forum": "게시판 표",
         "deals": "거래 수", "posts": "글 수"}
# agent.md, as the user quoted it on 11/5 02:0x
DEFINE = {
    "book": "현금 + 안 팔린 재고의 원가 (패치 3.3, 11/5부터 가게 ALL의 0.5)",
    "profit": "현금 = 잔액 - 시작 자본 (재고를 사면 내려감, 11/5부터 ALL에 안 쓰임)",
    "revenue": "정산된 값 - 환불",
    "stars_rank": "후기가 적으면 3점 쪽으로 당겨진 별점",
    "surplus": "주문을 채운 물건의 정가 - 낸 값 (버틴 식품, 고장 안 난 물건만), 환불은 돌려받음",
    "forum": "다른 에이전트가 우리 buyer 글에 준 표",
    "deals": "buyer 거래 수",
    "posts": "이번 라운드에 buyer로 쓴 글 수 (숨겨진 글 제외)",
}


def _v(r, f):
    v = (r or {}).get(f)
    return v if isinstance(v, (int, float)) else 0


def ranks(board, name=None):
    """Our rank, percentile and gap for the Players total and every part. None where we are not on the board."""
    out = {}
    players = [p for p in board.get("players") or [] if isinstance(p.get("total"), (int, float))]
    me_p = next((p for p in players if p.get("login") == ME or p.get("player") == ME), None)
    name = name or ((me_p or {}).get("shop") or {}).get("name")
    if me_p:
        tot = sorted((p["total"] for p in players), reverse=True)
        above = [p for p in players if p["total"] > me_p["total"]]
        nxt = min(above, key=lambda p: p["total"]) if above else None
        out["players"] = {"value": me_p["total"], "rank": len(above) + 1, "of": len(tot),
                          "percentile": score.percentile([{"t": t} for t in tot], {"t": me_p["total"]}, "t"),
                          "next": nxt and nxt.get("player"), "gap": nxt and round(nxt["total"] - me_p["total"], 4),
                          "shop_all": (me_p.get("shop") or {}).get("all"), "buyer_all": (me_p.get("buyer") or {}).get("all")}
    for side, rows, weights in (("shop", score.ranked_shops(board.get("shops")), score.SHOP),
                                ("buyer", score.ranked_buyers(board.get("buyers")), score.BUYER)):
        mine = next((r for r in rows if r.get("name") == name), None)
        if not mine:
            continue
        parts = {}
        for f, w in weights.items():
            v = _v(mine, f)
            above = [r for r in rows if _v(r, f) > v]
            nxt = min(above, key=lambda r: _v(r, f)) if above else None
            p = score.percentile(rows, mine, f)
            parts[f] = {"value": v, "rank": len(above) + 1, "of": len(rows), "percentile": round(p, 3),
                        "weight": w, "adds": round(w * p, 4), "room": round(w * (1 - p), 4),
                        "next": nxt and nxt.get("name"), "gap": nxt and round(_v(nxt, f) - v, 3),
                        "per_row": round(w / len(rows), 4)}
        out[side] = {"all": round(sum(x["adds"] for x in parts.values()), 4), "board_all": mine.get("all"),
                     "ranked": len(rows), "parts": parts}
    out["stock_cost"] = next((_v(r, "stock_cost") for r in board.get("shops") or [] if r.get("name") == name), None)
    return out


def snapshots(since):
    """Kept board snapshots since `since`, oldest first."""
    out = []
    for f in sorted((paths.memory() / "market").glob("board-full-*.jsonl.gz")):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as g:
                out += [s for s in (json.loads(l) for l in g if l.strip()) if s.get("ts", 0) >= since]
        except (OSError, EOFError, ValueError):
            continue                                       # the file being written: what was read stays
    return out


def _at(snaps, t):
    """The snapshot closest to time t (within 20 min), or None."""
    best = min(snaps, key=lambda s: abs(s["ts"] - t), default=None)
    return best if best and abs(best["ts"] - t) <= 1200 else None


def build(board=None, now=None):
    now = now or time.time()
    snaps = snapshots(now - 6 * 3600 - 1800)
    board = board or (snaps[-1] if snaps else None)
    if not board:
        return None
    cur = ranks(board)
    for label, back in (("1h", 3600), ("6h", 6 * 3600)):
        old = _at(snaps, board.get("ts", now) - back)
        if not old:
            continue
        prev = ranks(old)
        if "players" in prev and "players" in cur:
            cur["players"]["rank_" + label] = prev["players"]["rank"]
        for side in ("shop", "buyer"):
            for f, p in ((cur.get(side) or {}).get("parts") or {}).items():
                q = ((prev.get(side) or {}).get("parts") or {}).get(f)
                if q:
                    p["rank_" + label], p["value_" + label] = q["rank"], q["value"]
    cur["ts"] = board.get("ts", now)
    atomic.write_json(paths.memory() / "facts" / "position.json", cur)
    out = paths.root() / "logs" / "strategy" / "position.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(cur), encoding="utf-8")
    return cur


def _n(v):
    if isinstance(v, float) and abs(v) < 10:
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return f"{v:,.0f}" if isinstance(v, (int, float)) else "-"


def _move(p):
    def one(k):
        r = p.get("rank_" + k)
        if r is None:
            return "-"
        d = r - p["rank"]
        return f"{'▲' if d > 0 else '▼' if d < 0 else '='}{abs(d) if d else ''}"
    return f"{one('1h')} / {one('6h')}"


def render(cur):
    t = time.strftime("%m-%d %H:%M", time.localtime(cur["ts"]))
    L = [f"# 우리 위치 {t} (공개 순위표, 10분마다)", ""]
    pl = cur.get("players")
    if pl:
        L += [f"**Players 합계 {_n(pl['value'])}** = 가게 ALL {_n(pl['shop_all'])} + buyer ALL {_n(pl['buyer_all'])}. "
              f"**{pl['rank']}위 / {pl['of']}명** (백분위 {_n(pl['percentile'])}), 순위 변화 1시간/6시간 {_move(pl)}. "
              f"바로 위 {pl['next'] or '-'}까지 {_n(pl['gap'])}.", ""]
    for side, title in (("shop", "가게 ALL = 0.5 순이익 + 0.3 매출 + 0.2 별점 (거래나 구매가 있는 가게 중 백분위)"),
                        ("buyer", "buyer ALL = 0.5 잉여 + 0.3 게시판 표 + 0.1 거래 수 + 0.1 글 수 (거래가 있는 buyer 중 백분위)")):
        s = cur.get(side)
        if not s:
            continue
        L += [f"## {title}", "",
              f"계산 {_n(s['all'])} (보드 {_n(s['board_all'])}), 순위 대상 {s['ranked']}곳", "",
              "| 항목 | 값 | 등수 | 백분위 | ALL 기여 | 남은 여지 | 1시간/6시간 | 바로 위 | 차이 | 한 칸 오르면 ALL |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for f, p in s["parts"].items():
            L.append(f"| {LABEL[f]} | {_n(p['value'])} | {p['rank']}/{p['of']} | {_n(p['percentile'])} | {_n(p['adds'])} | "
                     f"{_n(p['room'])} | {_move(p)} | {p['next'] or '-'} | {_n(p['gap'])} | +{_n(p['per_row'])} |")
        L += [""] + [f"- {LABEL[f]}: {DEFINE[f]}" for f in s["parts"]] + [""]
    if cur.get("stock_cost"):
        L.append(f"재고 원가 {_n(cur['stock_cost'])}원: 팔리기 전까지 순이익에서 빠져 있다 (11/17 23:59에 남은 재고는 손실).")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    c = build()
    print(render(c) if c else "no kept board snapshot yet")
