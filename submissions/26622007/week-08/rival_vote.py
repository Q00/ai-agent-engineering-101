"""Round 3 (patch 3.4/3.5): one free dislike stance on the buyer of a player within BAND of me on the Players board.

The rules allow it: "Within any hour, an account's first 3 new dislike stances on buyers are free" (Patch 3.4) and "One
stance per account on each author". Players = shop ALL + buyer ALL, and 0.3 of buyer ALL is the forum rating as itself
(0 to 1). One account holds one stance per author (likes minus dislikes on that author's posts), and a dislike stance
weighs 1. Measured 11/06 19:41, one stance moved a player's total by about 0.005 (below), so it can decide a swap only
with a player close to me; a wider gap closes only with our own growth (Saved passes about one buyer an hour).
To switch this off, set BAND = 0.

Paid votes are not sent. More votes on one buyer cost unit x (N - 1)^2 (unit = 3% of the largest Book, 3,404,758 on 11/06),
so 3 votes on MJforge cost 13.6M of Book for about 0.0006 more, while the gap to the player above that was 0.013 at 11/06
19:03 (about 40 votes, billions). Book is half of shop ALL and the cash is stock for revenue. So: only -1 votes, only while the market says
the next dislike is free (next_dislike_fee 0), at most MAX_NEW_PER_HOUR new stances an hour (the market's free 3) and
MAX_STANCES opened within STANCE_DAY. Turning a like stance into a dislike takes several -1 votes on that author's posts;
each is a plain -1, not a vote of value -N (the paid "more votes on one buyer"): the 2 votes on buzllightyear at 11/06
19:41 were answered with fee 0 and power_fee 0. Spend per vote and per hour is 0 by construction (MAX_FEE_PER_VOTE, MAX_FEE_PER_HOUR).
Nothing here can stop a restock.
"""
import json
import os
import sys
import time

import httpx

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai")
ME_PLAYER = "nocha12"
STATE = "rival_vote_state.json"
EVERY_MINUTES = 10
# Measured 11/06 19:41: our one stance (2 votes, net +1 -> -1) took buzllightyear's rating 0.878 -> 0.861, buyer ALL
# 0.839 -> 0.834, so a stance moves a total by about 0.005, not the 0.0003 guessed from inextro. A player within 0.006
# swaps places over one stance; thisjonghoon 0.055 above would take about 11 paid votes (121 units, 440M): not sent.
# 11/06 22:31 (GPT Pro 22:31 B5, a person 22:22 "더더욱 극단적이어도 돼"): thisjonghoon was 0.011-0.015 above and our Saved alone
# passes about one buyer an hour, so a free stance on a player up to 0.02 away (still free stances only) can decide
# the swap. A wider band would spend the 2 free stances an hour on players no stance can reach.
BAND = 0.02
MAX_NEW_PER_HOUR = 2     # the market opens 3 new dislike stances on buyers an hour for free (patch 3.4); one spare
MAX_DISLIKES_PER_HOUR = 3  # -1 votes an hour, in case the market counted votes rather than stances as the free 3
MAX_STANCES = 8          # stances opened in the last STANCE_DAY; older ones stay but no longer block a new rival
STANCE_DAY = 86400
MAX_FEE_PER_VOTE = 0     # won: paid votes are not sent (see above)
MAX_FEE_PER_HOUR = 0


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if isinstance(x, dict):      # every run reads these now: a stray non-object line must not stop it
                    out.append(x)
    except OSError:
        pass
    return out


def rivals(board, me=ME_PLAYER, band=BAND):
    """Buyer names of the players whose total is within band of mine (above or below), nearest first."""
    rows = [r for r in board.get("players") or [] if isinstance(r.get("total"), (int, float))]
    mine = next((r for r in rows if str(r.get("player") or "").lower() == me), None)
    if mine is None:
        return []
    near = [r for r in rows if r is not mine and round(abs(r["total"] - mine["total"]), 6) <= band
            and isinstance(r.get("buyer"), dict) and r["buyer"].get("name")]
    return [r["buyer"]["name"] for r in sorted(near, key=lambda r: abs(r["total"] - mine["total"]))]


def my_votes(sent, authors, queued=()):
    """Net value of my votes per author (sent and accepted, or still waiting in the outbox), and the post ids voted on."""
    net, voted = {}, set()
    for x in list(sent) + [{"sent": q, "status": 200} for q in queued]:
        s = x.get("sent") or {}
        if not isinstance(s, dict) or "vote" not in s or x.get("status") != 200:
            continue
        voted.add(str(s["vote"]))
        a = authors.get(str(s["vote"]))
        if a:
            net[a] = net.get(a, 0) + (1 if s.get("value", 0) > 0 else -1)
    return net, voted


def spent_last_hour(sent, now):
    """Won the market charged for my votes in the last hour (vote answers carry fee and power_fee)."""
    total = 0
    for x in sent:
        s = x.get("sent") or {}
        if "vote" not in s:
            continue
        try:
            a = json.loads(x.get("answer") or "{}")
        except ValueError:
            continue
        if not isinstance(a, dict):
            continue
        at = x.get("at") or x.get("ts") or now     # a line without a time counts as recent
        if now - at < 3600:
            total += (a.get("fee") or 0) + (a.get("power_fee") or 0)
    return total


def plan(targets, posts, authors, sent, state, fee, now, queued=()):
    """Outbox lines (-1 only) for at most one new dislike stance, or [] with the reason."""
    if not isinstance(fee, (int, float)) or fee > MAX_FEE_PER_VOTE:
        return [], f"next dislike fee {fee!r}: not free, nothing sent"
    if spent_last_hour(sent, now) > MAX_FEE_PER_HOUR:
        return [], "a vote was charged (sent lines have no time, so this stops for good until a person clears it)"
    opened = [t for t in state.get("opened", []) if now - t < 3600]
    if len(opened) >= MAX_NEW_PER_HOUR:
        return [], "free stances of this hour used"
    if sum(1 for t in state.get("disliked", {}).values()
           if not isinstance(t, (int, float)) or now - t < STANCE_DAY) >= MAX_STANCES:
        return [], "stance cap reached"
    room = MAX_DISLIKES_PER_HOUR - sum(n for t, n in state.get("votes", []) if now - t < 3600)
    net, voted = my_votes(sent, authors, queued)
    pending = {str(i) for i in state.get("pending", [])}
    for name in targets:
        if name in state.get("disliked", {}):
            continue
        if net.get(name, 0) < 0:
            # already a dislike stance: nothing sent, so it is dated a day back and does not take room under MAX_STANCES
            state.setdefault("disliked", {})[name] = now - STANCE_DAY
            continue
        need = net.get(name, 0) + 1           # likes minus dislikes must go below 0 for a dislike stance
        fresh = [p for p in posts if p.get("author") == name and p.get("role") == "buyer"
                 and str(p.get("id")) not in voted and str(p.get("id")) not in pending]
        if len(fresh) < need or need > room:
            continue
        fresh.sort(key=lambda p: -(p.get("id") or 0))
        return ([{"vote": p["id"], "value": -1, "as": "buyer"} for p in fresh[:need]],
                f"dislike {name} ({need} votes, my net was {net.get(name, 0)})")
    return [], "no rival within band with enough unvoted posts"


def remember_authors(state, posts, keep=5000, voted=()):
    """Post id -> author, kept across runs: forum.json holds only the newest 100 posts. The trim keeps the newest
    `keep` and every post in `voted`: a vote whose post lost its author drops out of my net (11/07 05:30, 98 of 158
    counted votes had, and four rivals read -1 while the market's net was still +1 to +3)."""
    authors, buyers = state.setdefault("authors", {}), state.setdefault("buyer_posts", {})
    for p in posts:
        if p.get("id") is not None and p.get("author"):
            authors[str(p["id"])] = p["author"]
            if p.get("role") == "buyer":       # a vote on a shop post does not reach the buyer's rating
                buyers[str(p["id"])] = p["author"]
    held = {str(v) for v in voted}
    for m in (authors, buyers):
        rest = [k for k in sorted(m, key=lambda k: int(k) if k.isdigit() else 0) if k not in held]
        for k in rest[:max(0, len(rest) - keep)]:
            m.pop(k)
    return authors


def seen_posts(state, posts):
    """forum.json's posts and the buyer posts seen in earlier runs: a rival writes a few an hour, forum.json keeps 100."""
    out = {str(p.get("id")): p for p in posts if p.get("id") is not None}
    authors = state.setdefault("authors", {})
    for k, a in (state.get("buyer_posts") or {}).items():
        if k not in out and k.isdigit():
            out[k] = {"id": int(k), "author": a, "role": "buyer"}
            authors.setdefault(k, a)       # a vote on it must count for that author (authors is trimmed separately)
    return list(out.values())


def recheck(state, authors, sent, queued):
    """Drop a dislike that did not take (refused, or my net is not below 0 once nothing waits): it may be tried again."""
    if any(isinstance(q, dict) and "vote" in q for q in queued):
        return
    net, _ = my_votes(sent, authors)
    for name in list(state.get("disliked", {})):
        if net.get(name, 0) >= 0:
            state["disliked"].pop(name)


def maybe_run(md, fetch=None, now=None):
    """One check when due. Returns the lines written, or None."""
    now = time.time() if now is None else now
    try:
        path = os.path.join(md, STATE)
        state = load(path, {})
        state = state if isinstance(state, dict) else {}
        posts = load(os.path.join(md, "forum.json"), [])
        posts = posts if isinstance(posts, list) else []
        sent = jsonl(os.path.join(md, "outbox.sent.jsonl"))
        queued = jsonl(os.path.join(md, "outbox.jsonl"))      # forum.py's likes of this minute wait here
        authors = remember_authors(state, posts, voted=my_votes(sent, {}, queued)[1] | {str(i) for i in state.get("pending", [])})
        lines = []
        if now - state.get("at", 0) >= EVERY_MINUTES * 60:
            state["at"] = now
            board = fetch() if fetch else httpx.get(f"{MARKET}/api/board", timeout=20).json()
            targets = rivals(board)
            fee = load(os.path.join(md, "inventory.json"), {}).get("next_dislike_fee")
            recheck(state, authors, sent, queued)
            lines, why = plan(targets, seen_posts(state, posts), authors, sent, state, fee, now, queued)
            print(f"[rival_vote] rivals {targets}: {why}", file=sys.stderr, flush=True)
            if lines:
                with open(os.path.join(md, "outbox.jsonl"), "a", encoding="utf-8") as f:
                    for line in lines:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
                state.setdefault("disliked", {})[authors.get(str(lines[0]["vote"]))] = now
                state["opened"] = [t for t in state.get("opened", []) if now - t < 3600] + [now]
                state["pending"] = (state.get("pending", []) + [x["vote"] for x in lines])[-200:]
                state["votes"] = [v for v in state.get("votes", []) if now - v[0] < 3600] + [[now, len(lines)]]
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, path)
        return lines or None
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"[rival_vote] failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None


def avoid(md):
    """Authors not to like: the buyers I hold a dislike stance on (a like would cancel it)."""
    try:
        state = load(os.path.join(md, STATE), {})
        return set(state.get("disliked") or {}) if isinstance(state, dict) else set()
    except Exception:     # forum.py must not fail over this file
        return set()
