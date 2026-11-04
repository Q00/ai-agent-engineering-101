"""Bounded local collection, independent from 30-minute analysis; zero LLM calls."""

import time
from pathlib import Path
from market_state import read, write

ROOT = Path(__file__).resolve().parent


def settings():
    value = read(ROOT / "forum_capture.json")
    if value["collection_interval_seconds"] not in (60, 300, 600, 1800) or value["analysis_interval_seconds"] != 1800:
        raise ValueError("Unsupported forum collection/analysis interval")
    if not 100 <= value["max_posts"] <= 2000 or not 3600 <= value["retention_seconds"] <= 86400:
        raise ValueError("Forum buffer outside bounds")
    return value


def _capture(md, now=None):
    md = Path(md)
    now = time.time() if now is None else now
    config = settings()
    prior = read(md / "forum-buffer.json", {})
    if 0 <= now-prior.get("captured_at", -86400) < config["collection_interval_seconds"]:
        return prior
    source = md / "forum.json"
    if not -10 <= now-source.stat().st_mtime <= 120:
        return prior
    current = read(source)
    if not isinstance(current, list):
        raise ValueError("Forum source must be a list")
    merged = {p["id"]: p for p in prior.get("posts", [])}
    for post in current[-100:]:
        if (not isinstance(post, dict) or post.get("round") != 3 or type(post.get("id")) is not int
                or type(post.get("ts")) not in (int, float) or not 0 <= now-post["ts"] <= config["retention_seconds"]):
            continue
        entry = {k: post.get(k) for k in ("id", "ts", "round", "role", "likes", "dislikes", "score", "thread")}
        entry.update(text=str(post.get("text", ""))[:2000], author=str(post.get("author", ""))[:100],
                     owner=str(post.get("owner", ""))[:100], refs=(post.get("refs") or [])[:20])
        merged[post["id"]] = entry
    posts = sorted((p for p in merged.values() if 0 <= now-p["ts"] <= config["retention_seconds"]),
                   key=lambda p: (p["ts"], p["id"]))[-config["max_posts"]:]
    result = {"captured_at": now, "posts": posts, "model_calls": 0,
              "collection_interval_seconds": config["collection_interval_seconds"]}
    write(md / "forum-buffer.json", result)
    return result


def capture(md, now=None):
    from strategy_controller import acquire, release
    md = Path(md)
    md.mkdir(parents=True, exist_ok=True)
    handle = acquire(md / "forum-capture.lock")
    if handle is None:
        return read(md / "forum-buffer.json", {})
    try:
        return _capture(md, now)
    finally:
        release(handle)
