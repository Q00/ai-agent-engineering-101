"""Week 08 — review_team.py: the review team (Buyer unit, HQ 2026-11-04): it runs the forum. Buyer ALL weighs the
votes on our buyer posts 0.3 and the number of posts 0.1, so the forum is 40% of the buyer's score.

Code, not a model, and it does not guess (11-04: "Korean posts get more likes" was a guess the data did not back):
it runs an experiment. Every buyer post uses one arm -- a format (market summary or our purchase records) in a
language (ko or en). The Enuri tracker (enuri.track_forum) records each post's likes at 30 minutes; the review team
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
ARMS = ["summary:ko", "summary:en", "records:ko", "records:en"]
TAGS = {"summary:ko": "[시세]", "summary:en": "[Market]", "records:ko": "[구매 기록]", "records:en": "[Purchase records]"}
EPSILON, MIN_N, AGE = 0.2, 3, "30"
ME = "dragonash"


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def measured():
    """{arm: [likes at 30 min of each of our posts in that arm]} from the Enuri tracker."""
    out = {a: [] for a in ARMS}
    tag_arm = {t: a for a, t in TAGS.items()}
    for p in load(TRACK, {}).values():
        if p.get("author") == ME and AGE in (p.get("likes") or {}) and p.get("tag") in tag_arm:
            out[tag_arm[p["tag"]]].append(p["likes"][AGE])
    return out


def choose(kind, rng=random):
    """The arm for our next buyer post of this kind ('summary' or 'records'): (arm, why)."""
    m = measured()
    arms = [a for a in ARMS if a.startswith(kind + ":")]
    young = [a for a in arms if len(m[a]) < MIN_N]
    if young:
        arm, why = rng.choice(young), f"explore: fewer than {MIN_N} measured posts"
    elif rng.random() < EPSILON:
        arm, why = rng.choice(arms), "explore (20%)"
    else:
        arm = max(arms, key=lambda a: sum(m[a]) / len(m[a]))
        why = "best measured arm"
    st = load(STATE, {})
    st.update(last={"kind": kind, "arm": arm, "why": why, "ts": time.time()},
              arms={a: {"n": len(v), "mean_likes_30min": round(sum(v) / len(v), 2) if v else None} for a, v in m.items()})
    try:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return arm, why


def report():
    """For HR and the buyer team: what the experiment has measured so far."""
    st = load(STATE, {})
    return {"arms": st.get("arms") or {a: {"n": len(v), "mean_likes_30min": round(sum(v) / len(v), 2) if v else None}
                                       for a, v in measured().items()},
            "last": st.get("last")}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=1))
