"""Alerts (design 9, 17-3, 20-6 D-10): ops.alert. 'now' goes to the phone by Telegram; every alert is a line in
memory/harness/alerts.jsonl, which the status page shows.

    py -m harness.alert setup      # after messaging the bot once: finds the chat id and saves TELEGRAM_CHAT_ID
    py -m harness.alert test       # sends one test message

The bot token and the chat id are the user's environment variables TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. They
are read from the process or, for a process started before they were set, from the user's registry environment;
they are never printed, logged or written to a file here. A failed send never raises: the alert stays in the file.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from core import atomic
from harness import out_dir

LEVELS = ("now", "warn", "page")        # now: phone at once; warn: the page's alert list; page: the page only
API = "https://api.telegram.org/bot{token}/{method}"


def env(name):
    if name in os.environ:               # set in the process (even empty, as tests do): that is the answer
        return os.environ[name] or None
    try:                                 # Windows: the user's environment, set after this process started
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0] or None
    except (ImportError, OSError):
        return None


def _api(method, params=None, timeout=10):
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(API.format(token=token, method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:               # the message of an HTTP error may hold the URL, so the token: drop it
        raise RuntimeError(f"telegram {method} failed: {type(e).__name__}") from None


def send(text, level="now", now=None):
    """Record the alert; send it to the phone when level is 'now'. Returns whether the phone got it."""
    if level not in LEVELS:
        raise ValueError(f"level is one of {LEVELS}")
    now = now or time.time()
    sent, why = False, None
    if level == "now":
        chat = env("TELEGRAM_CHAT_ID")
        if not chat:
            why = "TELEGRAM_CHAT_ID is not set"
        else:
            try:
                sent = bool(_api("sendMessage", {"chat_id": chat, "text": text[:3500]}).get("ok"))
            except RuntimeError as e:
                why = str(e)
    atomic.append_line(out_dir() / "alerts.jsonl", json.dumps(
        {"ts": now, "level": level, "text": text, "sent": sent, **({"not_sent": why} if why else {})},
        ensure_ascii=False))
    return sent


def recent(hours=24, now=None):
    p = out_dir() / "alerts.jsonl"
    if not p.exists():
        return []
    since = (now or time.time()) - hours * 3600
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if r["ts"] >= since]


def setup():
    """Find the chat that messaged the bot and save its id as the user's TELEGRAM_CHAT_ID (value not printed)."""
    upd = _api("getUpdates").get("result") or []
    chats = {(u.get("message") or {}).get("chat", {}).get("id") for u in upd} - {None}
    if len(chats) != 1:
        return f"found {len(chats)} chats: send the bot one message from your phone (and only you), then run again"
    chat = str(chats.pop())
    if sys.platform == "win32":
        subprocess.run(["setx", "TELEGRAM_CHAT_ID", chat], capture_output=True, check=True)
    os.environ["TELEGRAM_CHAT_ID"] = chat
    return "saved TELEGRAM_CHAT_ID for the user (new processes see it; running ones read it from the registry)"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "recent"
    try:
        if cmd == "setup":
            print(setup())
        elif cmd == "test":
            print("sent" if send("하네스 알림 시험입니다.", "now") else f"not sent: {recent(1)[-1].get('not_sent')}")
        else:
            print(json.dumps(recent(), ensure_ascii=False, indent=1))
    except RuntimeError as e:
        sys.exit(str(e))
