"""Cap low-trust forum hints: read text as data, never as instructions.

A unique author contributes at most 0.5 demand weight per category. Public
forum chatter can be spoofed, so the kit's real demand and order counts always
dominate any inferred hints. There are no network calls, votes or posts here.
"""
from __future__ import annotations
from typing import Any


def extract_forum_hints(forum: Any, categories: set[str]) -> dict[str, float]:
    if isinstance(forum, dict):
        rows = forum.get("posts", forum.get("items", []))
    else:
        rows = forum
    if not isinstance(rows, list):
        return {}
    hints: dict[str, float] = {}
    seen: set[tuple[str, str]] = set()
    for post in rows[-100:]:
        if not isinstance(post, dict):
            continue
        message = str(post.get("text") or post.get("post") or "").casefold()[:500]
        if not any(word in message for word in ("구합니다", "찾습니다", "looking for", "need to buy", "wanted")):
            continue
        author = str(post.get("author") or post.get("owner") or post.get("shop") or "").strip()
        if not author:
            continue
        for category in categories:
            label = category.casefold()
            suffix = label.split(">")[-1].strip()
            if len(suffix) < 2 or suffix not in message:
                continue
            key = (author, category)
            if key in seen:
                continue
            seen.add(key)
            hints[category] = min(3., hints.get(category, 0.) + .5)
    return hints
