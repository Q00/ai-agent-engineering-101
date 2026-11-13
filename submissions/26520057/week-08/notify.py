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


def send(text):
    """Returns True when Slack accepted the message. Never raises."""
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
    ok = send(" ".join(sys.argv[1:]) or "AX 전략가 알림 시험")
    print("sent" if ok else "not sent (no webhook in runtime/strategist.env, or Slack refused)")
