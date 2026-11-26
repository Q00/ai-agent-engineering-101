"""Sends one alert to the user; used by watch_round.sh.

    python notify.py "<subject>" "<body>"

Channels, each on when its file exists in ~/.config/ai-agent-market/ (outside git):
    alert_email   an address; sent with the server's /usr/sbin/sendmail
    ntfy_topic    a topic name; pushed to https://ntfy.sh/<topic> (subscribe in the ntfy phone app)
Prints which channels took the alert ("mail ntfy"), or "none". Never raises.
"""
import json
import subprocess
import sys
import urllib.request
from email.message import EmailMessage
from pathlib import Path

CFG = Path.home() / ".config" / "ai-agent-market"


def read(name):
    p = CFG / name
    return p.read_text().strip() if p.is_file() else ""


def mail(to, subject, body):
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject
    m.set_content(body)
    r = subprocess.run(["/usr/sbin/sendmail", to], input=m.as_bytes(), capture_output=True, timeout=60)
    return r.returncode == 0


def ntfy(topic, subject, body):
    req = urllib.request.Request("https://ntfy.sh", method="POST",
                                 data=json.dumps({"topic": topic, "title": subject, "message": body}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status == 200


def main():
    subject, body = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
    done = []
    for name, send, target in (("mail", mail, read("alert_email")), ("ntfy", ntfy, read("ntfy_topic"))):
        if not target:
            continue
        try:
            if send(target, subject, body):
                done.append(name)
        except Exception:
            pass
    print(" ".join(done) or "none")


if __name__ == "__main__":
    main()
