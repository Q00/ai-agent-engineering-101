"""Week 08 — review_team.py: the review team (Buyer unit, HQ 2026-11-04): it runs the forum. Buyer ALL weighs the
votes on our buyer posts 0.3 and the number of posts 0.1, so the forum is 40% of the buyer's score.

Code, not a model, and it does not guess (11-04: "Korean posts get more likes" was a guess the data did not back):
it runs an experiment. Every buyer post (Korean and English, HQ 11-04) uses one arm, a format: a market summary or
our purchase records, each as Korean+English text or as JSON for the agents that read it. The Enuri tracker (enuri.track_forum) records each post's likes at 15 minutes; the review team
keeps the mean per arm and picks the next post's arm epsilon-greedy (best arm 80%, a random arm 20%; an arm with
fewer than MIN_N measured posts is tried first). Posting stays at the market's rate (forum.py), always ledger-cited.

    private/review_team.json   arms with their measured likes, the last choice, a short report
"""
import json
import random
import time
from pathlib import Path

HERE = Path(__file__).parent
STATE = HERE / "private" / "review_team.json"
TRACK = HERE / "private" / "enuri" / "forum_track.json"
ARMS = ["summary:json", "summary:text", "summary:short", "records:json", "records:text", "records:short"]
# format x style: JSON first, Korean+English text first, or three Korean lines with 5 refs (11-04 review: our posts
# draw ~1 like fewer than the top authors' in every format -- is it length?). The tracker tells the styles apart by
# tag and length, so each arm is scored on its own posts.
TAGS = {"summary:json": "{market_summary}", "summary:text": "[시세]", "summary:short": "[시세]",
        "records:json": "{purchase_records}", "records:text": "[구매 기록]", "records:short": "[구매 기록]"}
EPSILON, MIN_N, AGE = 0.2, 3, "15"   # 30 min never came: the forum's newest-100 list drops our posts before that
ME = "dragonash"


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def measured():
    """{arm: [likes at 30 min of each of our posts in that arm]} from the Enuri tracker."""
    out = {a: [] for a in ARMS}
    for p in load(TRACK, {}).values():
        if p.get("author") != ME or AGE not in (p.get("likes") or {}):
            continue
        for arm, tag in TAGS.items():               # a text post is long, a short one is short (tracker's "long")
            if p.get("tag") == tag and (not arm.endswith((":text", ":short")) or
                                        arm.endswith(":text") == ((p.get("len") or 0) >= 400)):
                out[arm].append(p["likes"][AGE])
                break
    return out


def choose(kinds, rng=random):
    """Which format the next buyer post uses, among the kinds ready now: (arm, why)."""
    m = measured()
    arms = [a for a in ARMS if a.split(":")[0] in kinds] or list(ARMS)
    young = [a for a in arms if len(m[a]) < MIN_N]
    if young:
        arm, why = rng.choice(young), f"explore: fewer than {MIN_N} measured posts"
    elif rng.random() < EPSILON:
        arm, why = rng.choice(arms), "explore (20%)"
    else:
        arm = max(arms, key=lambda a: sum(m[a]) / len(m[a]))
        why = "best measured arm"
    st = load(STATE, {})
    st.update(last={"arm": arm, "why": why, "ts": time.time()},
              arms={a: {"n": len(v), "mean_likes_15min": round(sum(v) / len(v), 2) if v else None} for a, v in m.items()})
    try:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return arm, why


def report():
    """For HR and the buyer team: what the experiment has measured so far."""
    st = load(STATE, {})
    return {"arms": st.get("arms") or {a: {"n": len(v), "mean_likes_15min": round(sum(v) / len(v), 2) if v else None}
                                       for a, v in measured().items()},
            "last": st.get("last")}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=1))
