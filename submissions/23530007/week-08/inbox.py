"""The user's messages from the phone (user 11-08: 'can you receive what I send on ntfy?').

    python inbox.py                 one poll
    python inbox.py --loop 14 30    poll every 30 s for 14 min (watch_round.sh starts it each 15 min tick)

The user publishes to the same ntfy topic the alerts go to (user 11-08: 'use the alert one'; its name stays outside
git in ~/.config/ai-agent-market/ntfy_topic and must not be shared, since anyone who knows a topic can post to it).
Our own alerts carry a title starting "[yms979]" and are skipped. Each new message from the user is
  1. kept in .market/user_inbox.jsonl (the operator, Claude Code, reads it and answers there),
  2. passed to the agents as a note from the user: "상점:" / "shop:" -> the shop strategist, "구매:" / "buyer:" -> the
     buyer strategist, anything else -> both (operator_to_shop.json / operator_to_buyer.json, read on their next run),
  3. acknowledged on the user's alert topic (notify.py).
"""
import fcntl
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOPIC_FILE = Path.home() / ".config" / "ai-agent-market" / "ntfy_topic"
INBOX = HERE / ".market" / "user_inbox.jsonl"
STATE = HERE / ".market" / "user_inbox_state.json"
LOCK = HERE / ".market" / "inbox.lock"
NOTES = {"shop": HERE / "operator_to_shop.json", "buyer": HERE / "operator_to_buyer.json"}
KEEP_CHARS = 4000


def route(text):
    low = text.strip().lower()
    for prefix, who in (("상점:", ["shop"]), ("shop:", ["shop"]), ("구매:", ["buyer"]), ("buyer:", ["buyer"])):
        if low.startswith(prefix):
            return who, text.strip()[len(prefix):].strip()
    return ["shop", "buyer"], text.strip()


def add_note(who, text, t):
    p = NOTES[who]
    try:
        old = json.loads(p.read_text()).get("note") or ""
    except (OSError, ValueError):
        old = ""
    note = (old + f"\n\nFrom the user (phone, {time.strftime('%m-%d %H:%M UTC', time.gmtime(t))}): {text}").strip()
    p.write_text(json.dumps({"t": time.time(), "note": note[-KEEP_CHARS:]}, ensure_ascii=False, indent=1), encoding="utf-8")


def poll():
    if not TOPIC_FILE.is_file():
        return 0
    topic = TOPIC_FILE.read_text().strip()
    try:
        st = json.loads(STATE.read_text())
    except (OSError, ValueError):
        st = {}
    if not st.get("last_id"):                               # first run: start from now, not from old alerts
        st["last_id"] = str(int(time.time()))
        STATE.write_text(json.dumps(st))
        return 0
    since = st["last_id"]
    with urllib.request.urlopen(f"https://ntfy.sh/{topic}/json?poll=1&since={since}", timeout=30) as r:
        lines = r.read().decode("utf-8", "replace").splitlines()
    n = 0
    for line in lines:
        m = json.loads(line)
        if m.get("event") != "message" or not (m.get("message") or "").strip():
            continue
        st["last_id"] = m["id"]
        if str(m.get("title") or "").startswith("[yms979]") or m["message"].strip().lower() in ("triggered", "test"):
            continue
        who, text = route(m["message"])
        with open(INBOX, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": m.get("time"), "id": m["id"], "to": who, "text": text}, ensure_ascii=False) + "\n")
        for w in who:
            add_note(w, text, m.get("time") or time.time())
        names = {"shop": "상점 에이전트", "buyer": "구매 에이전트"}
        subprocess.run([sys.executable, str(HERE / "notify.py"), "[yms979] 메시지 받음",
                        f"\"{text[:120]}\"\n→ {', '.join(names[w] for w in who)} 다음 판단(30분 안)에 전달했습니다. "
                        "운영자(Claude Code)도 확인합니다."], capture_output=True, text=True)
        n += 1
    STATE.write_text(json.dumps(st))
    return n


def main():
    minutes, every = 0, 0
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        minutes, every = float(sys.argv[i + 1]), float(sys.argv[i + 2])
    with open(LOCK, "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        end = time.time() + minutes * 60
        while True:
            try:
                poll()
            except Exception:
                pass
            if time.time() + every >= end:
                break
            time.sleep(every)


if __name__ == "__main__":
    main()
