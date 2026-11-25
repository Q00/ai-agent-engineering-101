"""Measure recent post volume against local snapshot time, without network/LLM."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

from market_state import read, write

ROOT = Path(__file__).resolve().parent


def analyze(md, now=None):
    now = time.time() if now is None else now
    path = md / "forum.json"
    end = path.stat().st_mtime
    posts = {p["id"]: p for p in read(path) if isinstance(p, dict) and type(p.get("id")) is int
             and p.get("round") == 3 and type(p.get("ts")) in (int, float) and p["ts"] <= end}
    oldest = min((p["ts"] for p in posts.values()), default=end)
    windows = []
    for minutes in (1, 5, 10):
        selected = [p for p in posts.values() if end-minutes*60 < p["ts"] <= end]
        windows.append({"minutes": minutes, "posts": len(selected), "per_minute": len(selected)/minutes,
                        "buyer": sum(p.get("role") == "buyer" for p in selected),
                        "shop": sum(p.get("role") == "shop" for p in selected),
                        "full_window_covered": oldest <= end-minutes*60})
    return {"measured_at": now, "snapshot_at": end, "snapshot_age_seconds": now-end,
            "snapshot_posts": len(posts), "oldest_post_at": oldest, "snapshot_bytes": path.stat().st_size,
            "windows": windows, "cadences": [{"minutes": minutes, "file_checks_per_day": 1440//minutes,
                "analysis_per_day": 48, "direct_additional_llm_tokens": 0,
                "additional_forum_api_requests": 0} for minutes in (1, 5, 10, 30)],
            "cost_basis": "Static call-path analysis: local Python collection/analysis/templates only."
                          " Negotiations induced by forum activity can add LLM tokens; not estimated here."}


if __name__ == "__main__":
    result = analyze(ROOT / ".market")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    write(ROOT / "logs/forum-traffic" / (stamp + "-" + uuid4().hex[:8] + ".json"), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
