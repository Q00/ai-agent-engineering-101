"""Forum post queue with the kernel's forum rules (human code, not editable by the model).

    from kernel import forum
    forum.queue(root, [{"post": "...", "as": "shop" | "buyer"}])

Whether and what to post is the operator's decision. The kernel only enforces DESIGN 10:
  - posts only, no votes (malicious voting is banned and votes are not needed);
  - at most one post per RATE_S per role, the market limit, tracked in <root>/a2/forum_state.json;
  - text that looks like prompt injection aimed at other agents is rejected (conservative pattern list);
  - secrets and identities are masked with common.mask, then the text is cut to LEN_CAP.
Each post appends one trajectory row of kind "forum" with ok, why and length, never the text. Stdlib only.
"""

import json
import os
import re

from kernel import common as c
from kernel.trajectory import Trajectory

# Step 5 (live switch) flips this to True: accepted posts are then written as {"post", "as"} lines to
# .market/outbox.jsonl, which the kit sends. Until then they go to a2/outbox_pending.jsonl and nothing is sent.
LIVE = False
ROLES = ("shop", "buyer")
RATE_S = 300
LEN_CAP = 1000
INJECTION = (
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "disregard previous",
    "system prompt",
    "you must",
    "assistant:",
    "system:",
    "developer:",
    "[inst]",
    "<|",
    "이전 지시",
    "위의 지시",
    "앞의 지시",
    "지시를 무시",
    "명령을 무시",
    "지시는 무시",
    "시스템 프롬프트",
    "반드시 따라",
    "너는 반드시",
    "당신은 반드시",
)
_ROLE_TAG = re.compile(r"<\s*/?\s*(system|assistant|user|developer|tool)\b", re.I)


def looks_like_injection(text):
    low = text.lower()
    return any(p in low for p in INJECTION) or bool(_ROLE_TAG.search(text))


def _check(item):
    """None when the item may be posted (rate limit aside), else the reason."""
    if not isinstance(item, dict) or set(item) - {"post", "as"}:
        return "posts only (no votes or other fields)"
    if item.get("as") not in ROLES:
        return "as must be shop or buyer"
    text = item.get("post")
    if not isinstance(text, str) or not text.strip():
        return "empty post"
    if looks_like_injection(text):
        return "looks like prompt injection"
    return None


def queue(root, posts, now=None):
    """Check each post and queue the accepted ones. Returns [{"ok", "why"}] in input order."""
    now = c.now_ts() if now is None else now
    login = c.owner_login(root)
    secrets = (login,) if login else ()
    state_path = os.path.join(root, "a2/forum_state.json")
    state = c.load_json(state_path, {}) or {}
    last = dict(state.get("last") or {})
    out_path = os.path.join(
        root, ".market/outbox.jsonl" if LIVE else "a2/outbox_pending.jsonl"
    )
    traj = Trajectory(os.path.join(root, "a2/trajectory.jsonl"), secrets)
    res = []
    for item in posts or []:
        why = _check(item)
        text = ""
        if why is None:
            prev = last.get(item["as"])
            if isinstance(prev, (int, float)) and now - prev < RATE_S:
                why = f"rate limit: one post per {RATE_S} s per role"
        if why is None:
            text = c.mask(item["post"].strip(), secrets)[:LEN_CAP]
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps({"post": text, "as": item["as"]}, ensure_ascii=False)
                    + "\n"
                )
            last[item["as"]] = now
        ok = why is None
        res.append({"ok": ok, "why": "queued" if ok else why})
        traj.append("kernel", "forum", ok=ok, why=res[-1]["why"], length=len(text))
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"last": last}, f)
    return res
