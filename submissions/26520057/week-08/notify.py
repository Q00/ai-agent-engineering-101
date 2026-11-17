"""Send a short message to the student's Slack channel (incoming webhook, one way).

The webhook URL lives in runtime/strategist.env as SLACK_WEBHOOK_URL and is never committed.
Without it, nothing is sent and nothing fails.
  python notify.py "text"      send one message (used for the setup test)
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def webhook():
    url = os.environ.get("SLACK_WEBHOOK_URL")
    env = ROOT / "runtime/strategist.env"
    if not url and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("SLACK_WEBHOOK_URL="):
                url = line.split("=", 1)[1].strip()
    return url if url and url.startswith("https://hooks.slack.com/") else None


# 다나 (2026-11-17): Slack only when she has to decide something. Muted messages are kept in logs/notify-muted.jsonl.
NEEDS_DANA = (":raising_hand:", "승인", "API 예산")


def needs_dana(text):
    if not any(m in text for m in NEEDS_DANA):
        return ""
    lines = [l for l in text.splitlines() if any(m in l for m in NEEDS_DANA) or l[:3].rstrip(". ").isdigit()]
    return "\n".join(lines)


def muted(text, why):
    import time
    try:
        with open(ROOT / "logs/notify-muted.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M KST"), "why": why, "text": text[:1500]}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return False


def send(text, force=False):
    """Returns True when Slack accepted the message. Never raises."""
    if not force:
        key = needs_dana(text)
        if not key:
            return muted(text, "no decision for 다나")
        import hashlib, time
        state = ROOT / "logs/notify-last.json"
        try:
            last = json.loads(state.read_text())
        except (OSError, ValueError):
            last = {}
        h = hashlib.sha1(key.encode()).hexdigest()
        if last.get(h, 0) > time.time() - 12 * 3600:
            return muted(text, "same request sent in the last 12 h")
        last[h] = time.time()
        try:
            state.write_text(json.dumps(last))
        except OSError:
            pass
    url = webhook()
    if not url:
        return False
    try:
        request = urllib.request.Request(url, data=json.dumps({"text": text[:3500]}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    ok = send(" ".join(sys.argv[1:]) or "AX 전략가 알림 시험", force=True)
    print("sent" if ok else "not sent (no webhook in runtime/strategist.env, or Slack refused)")
