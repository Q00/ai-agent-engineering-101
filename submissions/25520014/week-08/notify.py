"""Week 08 notify: send a text to the owner's Telegram chat. Used by both managers (hourly reports) and run.sh's
watchdog (a process restarted).

    python notify.py "seller restarted"

TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID come from the environment or from .env next to this file (never committed).
Without them, or when Telegram does not answer, it logs and returns False: a report must never stop a manager.
Plain text, cut into pieces under Telegram's 4096-character limit.
"""
import os
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
LIMIT = 4000


def env():
    vals = {}
    try:
        for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    get = lambda k: os.environ.get(k) or vals.get(k)
    return get("TELEGRAM_BOT_TOKEN"), get("TELEGRAM_CHAT_ID")


def send(text):
    token, chat = env()
    if not (token and chat):
        print("[notify] no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID: not sent", flush=True)
        return False
    ok = True
    for i in range(0, max(len(text), 1), LIMIT):
        try:
            r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                           data={"chat_id": chat, "text": text[i:i + LIMIT]}, timeout=15)
            ok = ok and r.status_code == 200
            if r.status_code != 200:
                print(f"[notify] telegram answered {r.status_code}: {r.text[:150]}", flush=True)
        except httpx.HTTPError as e:                  # the token is in the URL: print the error type only
            print(f"[notify] telegram unreachable ({type(e).__name__})", flush=True)
            ok = False
    return ok


if __name__ == "__main__":
    sys.exit(0 if send(" ".join(sys.argv[1:]) or "test") else 1)
