"""REPORT.md (3) from the final Round 3 board (week-08 midterm; run after 11/17 23:59, when the market is paused).

  1. the board: --board PATH (a saved copy), else /api/board?round=3 now (paused market = the 23:59 board), saved to
     reports/board_final_20261117.json; refuses a live fetch before 23:59 unless --force
  2. Players Class view: students sorted by student_total, my rank / n, total, shop and buyer parts, percentile
  3. my shop / buyer rows (deals, revenue, Book, stars, Saved, forum ...)
  4. from logs/market-kit.log(.1) (dated backwards as depts/claim_full.kit_lines) up to 23:59: my shop settlements,
     my buyer's payments, the refusal reasons, with one quoted line each
  5. the week 10 baseline (11/10 18:00, rebuilt from logs/board_full.jsonl and market.db) next to the final values;
     the two board snapshots go to reports/baseline_1110.json (not with --dry)
Replaces the text between <!-- FINAL:BEGIN --> and <!-- FINAL:END --> in REPORT.md (--dry prints it only).
    python tools/final_numbers.py [--board PATH] [--dry] [--force]
"""
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
REPORT = os.path.join(HERE, "REPORT.md")
SAVED = os.path.join(HERE, "reports", "board_final_20261117.json")
END = datetime(2026, 11, 17, 23, 59, 59).timestamp()
T10 = datetime(2026, 11, 10, 18, 0).timestamp()
ME_PLAYER, ME = "Choo-chu", "choo-chu"
BEGIN_MARK, END_MARK = "<!-- FINAL:BEGIN -->", "<!-- FINAL:END -->"


def board(path=None, force=False):
    if path:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    if time.time() < END and not force:
        sys.exit("before 23:59: pass --board PATH or --force")
    import httpx
    j = httpx.get("https://market.ourolabs.ai/api/board", params={"round": 3}, timeout=60).json()
    os.makedirs(os.path.dirname(SAVED), exist_ok=True)
    with open(SAVED, "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False)
    return j, SAVED


def class_view(j):
    st = sorted((p for p in j.get("players") or [] if p.get("student")), key=lambda p: -(p.get("student_total") or 0))
    i = next(k for k, p in enumerate(st) if p.get("player") == ME_PLAYER)
    return i + 1, len(st), st[i], st[:5]


def log_counts(now, paths=None):
    """{class: (count, last line)} of the kit's acp lines up to END."""
    from depts import claim_full
    rows = claim_full.kit_lines(now, "[acp]", paths)
    out = {}
    def put(k, ln):
        n, _ = out.get(k, (0, None))
        out[k] = (n + 1, ln.strip())
    for ts, ln in rows:
        if ts > END:
            continue
        if "[acp] settled" in ln:
            put("가게 정산(`[acp] settled`)", ln)
        elif "[acp] paid" in ln:
            put("구매자 결제(`[acp] paid`)", ln)
        elif re.search(r"not paying: .*shelf life", ln):
            put("구매자 결제 거부: 유통기한 부족", ln)
        elif "not paying: grade" in ln:
            put("구매자 결제 거부: 등급", ln)
        elif "not paying" in ln:
            put("구매자 결제 거부: 기타", ln)
        elif "checkout refused" in ln:
            put("가게 결제 거절: 유닛이 이 주문의 재고에 없음", ln)
    first = next((ts for ts, _ in rows), None)
    return out, first


BASE_FILE = os.path.join(HERE, "reports", "baseline_1110.json")


def save_baseline(rows, j, path=None, max_bytes=1_000_000, forum=None):
    """11/15 (person): the logs/board_full.jsonl row nearest 11/10 18:00 and the last row up to 23:59 ->
    reports/baseline_1110.json; over max_bytes only my rows and the students' shop / buyer rows are kept (board_full
    has no Players table: the student names come from the final board's players)."""
    r0 = min(rows, key=lambda r: abs(r["ts"] - T10))
    r1 = max((r for r in rows if r["ts"] <= END), key=lambda r: r["ts"])
    out = {"source": "logs/board_full.jsonl (10-min board snapshots)", "t1110": r0, "final": r1}
    if forum:                                   # 11/17 (person): my forum totals, both times, with how they are counted
        out["forum"] = forum
    blob = json.dumps(out, ensure_ascii=False)
    if len(blob.encode("utf-8")) > max_bytes:
        stud = [p for p in j.get("players") or [] if p.get("student")]
        shops = {(p.get("shop") or {}).get("name") for p in stud} | {ME}
        buyers = {(p.get("buyer") or {}).get("name") for p in stud} | {ME}
        cut = lambda r: {"ts": r["ts"], "s": {k: v for k, v in r["s"].items() if k in shops},
                         "b": {k: v for k, v in r["b"].items() if k in buyers}}
        out.update(t1110=cut(r0), final=cut(r1), note="only my rows and the students' shop / buyer rows (over 1 MB)")
        blob = json.dumps(out, ensure_ascii=False)
    with open(path or BASE_FILE, "w", encoding="utf-8") as f:
        f.write(blob)
    return r0


def baseline(j, save=True):
    rows = []
    with open(os.path.join(HERE, "logs", "board_full.jsonl"), encoding="utf-8") as f:
        for ln in f:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                pass
    r0 = min(rows, key=lambda r: abs(r["ts"] - T10))
    s0 = r0["s"].get(ME, {})
    s1 = next(x for x in j.get("shops") or [] if x.get("name") == ME)
    d = sqlite3.connect(f"file:{os.path.join(HERE, 'market.db')}?mode=ro", uri=True)
    try:
        def forum(t):
            mine = d.execute("SELECT id, likes FROM forum_posts WHERE author=? AND ts<?", (ME, t)).fetchall()
            lk = 0
            for i, cur in mine:
                v = d.execute("SELECT likes FROM forum_votes WHERE id=? AND ts<=? ORDER BY ts DESC LIMIT 1", (i, t)).fetchone()
                lk += (v[0] if v else (cur if t >= END else 0)) or 0
            rep = d.execute("SELECT COUNT(*) FROM forum_posts WHERE ts<? AND author!=? AND thread IN (SELECT id FROM "
                            "forum_posts WHERE author=?)", (t, ME, ME)).fetchone()[0]
            return len(mine), lk, rep
        f0, f1 = forum(T10), forum(END)
    finally:
        d.close()
    if save:
        save_baseline(rows, j, forum={
            "how": ("market.db forum_posts / forum_votes, author choo-chu. posts: my posts before the time; likes: "
                    "each post's last recorded like count at or before the time (the current count at the end); "
                    "replies: other authors' posts before the time whose thread is one of my posts"),
            "t1110": {"at": "2026-11-10 18:00", "posts": f0[0], "likes": f0[1], "replies": f0[2]},
            "final": {"at": "2026-11-17 23:59", "posts": f1[0], "likes": f1[1], "replies": f1[2]}})
    return [("가게 매출", f"{s0.get('revenue', 0):,}", f"{s1.get('revenue', 0):,}"),
            ("가게 정산", f"{s0.get('deals', 0):,}", f"{s1.get('deals', 0):,}"),
            ("우리 포럼 글이 받은 좋아요", f"{f0[1]:,} (글 {f0[0]:,}개)", f"{f1[1]:,} (글 {f1[0]:,}개)"),
            ("우리 글에 달린 답글", f"{f0[2]:,}", f"{f1[2]:,}")]


def text(j, src, now, log_paths=None, save=True):
    rank, n, me, top = class_view(j)
    s = next(x for x in j.get("shops") or [] if x.get("name") == ME)
    b = next(x for x in j.get("buyers") or [] if x.get("name") == ME)
    lc, first = log_counts(now, log_paths)
    L = [BEGIN_MARK,
         f"아래 숫자는 Round 3 마감 board(11/17 23:59, 저장본 {os.path.relpath(src, HERE).replace(os.sep, '/')})에서 tools/final_numbers.py로 뽑았다.",
         "",
         "| 항목 | 값 |", "|---|---|",
         f"| Players Class view 순위 | {rank}위 / {n}명, 합계 {me.get('student_total'):.3f} (shop {me.get('student_shop')}, "
         f"buyer {me.get('student_buyer')}), 학생 백분위 {me.get('student_pct')} |",
         f"| 가게 | 정산 {s.get('deals', 0):,}건, 매출 {s.get('revenue', 0):,}, Book {s.get('book', 0):,}, 별점 {s.get('stars')}"
         f"(순위용 {s.get('stars_rank')}), 리뷰 {s.get('reviews', 0):,}, 등급 주장 {s.get('claims', 0):,}건 중 정직 "
         f"{s.get('honest_claims', 0):,}건, 고장 {s.get('broken', 0):,}건 중 환불 {s.get('refunds', 0):,}건, ALL {s.get('all')} |",
         f"| 구매자 | 정산 {b.get('deals', 0):,}건, 주문 {b.get('orders', 0):,}건, Saved {b.get('saved')}, 포럼 글 "
         f"{b.get('posts', 0):,}, 포럼 평점 {b.get('forum')}, ALL {b.get('all')} |",
         f"| 학생 상위 5 | " + ", ".join(f"{p['player']} {p['student_total']:.3f}" for p in top) + " |",
         "",
         f"결제·거부 사유(logs/market-kit.log와 .1, {datetime.fromtimestamp(first):%m/%d %H:%M} ~ 11/17 23:59)"
         if first else "결제·거부 사유(logs/market-kit.log와 .1)",
         "", "| 구분 | 건수 |", "|---|---|"]
    order = ["가게 정산(`[acp] settled`)", "구매자 결제(`[acp] paid`)", "구매자 결제 거부: 유통기한 부족",
             "구매자 결제 거부: 등급", "구매자 결제 거부: 기타", "가게 결제 거절: 유닛이 이 주문의 재고에 없음"]
    for k in order:
        L.append(f"| {k} | {lc.get(k, (0, None))[0]:,} |")
    L += ["", "로그 인용", "", "```"]
    L += [lc[k][1][:230] for k in order if k in lc]
    L += ["```", "",
          "market-kit.log는 키트 회전 때문에 11/16 06:55 이후만 남아 있고, 그 전 기록은 kit-console 로그, market.db, 일일",
          "보고서(reports/daily_*.md)에 있다. kit-console 로그에서 정산·결제 거부 줄만 날짜를 붙여 추린 것이",
          "logs/kit-settle-extract.txt다(11/05 ~ 11/17).", "",
          "10주차 기준 숫자와 비교. 10주차 LAB에서 적어 둔 기록이 저장소에 없어서, 11/10 18:00 시점 값을 market.db와",
          "logs/board_full.jsonl에서 다시 계산했다(재구성 값).", "",
          "| 숫자 | 11/10 18:00(재구성) | 11/17 23:59 |", "|---|---|---|"]
    L += [f"| {a} | {x} | {y} |" for a, x, y in baseline(j, save=save)]
    L += ["", "재구성 값 (11/10 18:00, market.db·board 스냅샷 기준). 출처는 reports/baseline_1110.json (board 스냅샷 "
              "두 줄과 우리 포럼 글의 좋아요·답글 합계, 집계 기준 포함)."]
    L.append(END_MARK)
    return "\n".join(L)


def replace(report_text, block):
    i, k = report_text.find(BEGIN_MARK), report_text.find(END_MARK)
    if i < 0 or k < 0:
        raise ValueError("FINAL markers not found in REPORT.md")
    return report_text[:i] + block + report_text[k + len(END_MARK):]


def main():
    path = sys.argv[sys.argv.index("--board") + 1] if "--board" in sys.argv else None
    j, src = board(path, "--force" in sys.argv)
    block = text(j, src, time.time(), save="--dry" not in sys.argv)   # --dry writes nothing
    if "--dry" in sys.argv:
        print(block)
        return
    with open(REPORT, encoding="utf-8") as f:
        t = f.read()
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(replace(t, block))
    print(f"REPORT.md (3) updated from {src}")


if __name__ == "__main__":
    main()
