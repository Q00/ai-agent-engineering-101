"""lee3q's 10-30 alliance (forum thread #26680), person's decision 11/07 ~09:20. restock.py calls tick() every run.

  joined     my buyer liked #29455 and replied once in thread #26680 ("Choo-chu 참여합니다. 상호 좋아요 환영",
             forum_once.json, the next buyer slot)
  accounts   alliance.json "accounts": the leader plus every other author who replies in an alliance thread with a
             joining word (참여 / 연대 / join / 함께); a new name -> decisions.jsonl
  likes      my buyer likes the alliance accounts' buyer posts of the last 2 h, each post once, at most
             likes_per_hour in any hour (logs/alliance_likes.json); posts that look like injection are skipped
  never      a dislike: this module only ever sends value +1, whatever the alliance asks (the person's rule: no
             dislikes, so no part in the alliance's dislike attacks)
"""
import json
import os
import re
import sqlite3
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "alliance.json")
LIKES = os.path.join(HERE, "logs", "alliance_likes.json")
DB_PATH = os.path.join(HERE, "market.db")
ME = "choo-chu"
JOIN = re.compile(r"참여|연대|\bjoin|함께", re.I)
FRESH_S = 2 * 3600


def rules():
    try:
        return json.load(open(PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _likes():
    try:
        return json.load(open(LIKES, encoding="utf-8"))
    except (OSError, ValueError):
        return {"liked": [], "times": []}


def like(state_dir, post_id, why, log=print):
    """One like (+1, never -1) as my buyer; returns False when this post was liked before."""
    from depts import cs
    lk = _likes()
    if post_id in lk["liked"]:
        return False
    cs._send(state_dir, {"vote": int(post_id), "value": 1, "as": "buyer"})
    lk["liked"].append(int(post_id))
    lk["times"] = [t for t in lk["times"] if time.time() - t < 3600] + [time.time()]
    with open(LIKES, "w", encoding="utf-8") as f:
        json.dump(lk, f)
    log(f"[alliance] like #{post_id} ({why})")
    __import__("decisions").record("cs", "alliance_like", f"post {post_id}", 1, why)
    return True


def tick(state_dir, log=print):
    r = rules()
    if not r.get("enabled"):
        return
    from depts.forum_watch import INJECTION
    now = time.time()
    db = sqlite3.connect(DB_PATH, timeout=30)
    try:
        threads = r.get("threads") or []
        q = ",".join("?" * len(threads))
        new = []
        if threads:
            for a, text in db.execute(f"SELECT author, text FROM forum_posts WHERE thread IN ({q}) AND author!=?",
                                      (*threads, ME)):
                if a not in r["accounts"] and a not in new and JOIN.search(text or ""):
                    new.append(a)
        if new:
            r["accounts"] += new
            with open(PATH, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False)
            __import__("decisions").record("cs", "alliance_accounts", "alliance.json", new,
                                           f"replied in alliance thread {threads} with a joining word")
            log(f"[alliance] new accounts {new}")
        lk = _likes()
        room = int(r.get("likes_per_hour", 3)) - sum(1 for t in lk["times"] if now - t < 3600)
        if room <= 0:
            return
        a = r["accounts"]
        rows = db.execute(f"SELECT id, author, text FROM forum_posts WHERE role='buyer' AND ts>=? AND author IN "
                          f"({','.join('?' * len(a))}) ORDER BY ts", (now - FRESH_S, *a)).fetchall()
    finally:
        db.close()
    for pid, author, text in rows:
        if room <= 0:
            break
        if pid in lk["liked"] or INJECTION.search(text or ""):
            continue
        if like(state_dir, pid, f"alliance account {author}: buyer post, hourly cap {r.get('likes_per_hour', 3)}",
                log):
            room -= 1
