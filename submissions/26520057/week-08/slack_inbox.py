"""Read Dana's replies in the Slack channel and act on the narrow set below. Runs every 2 minutes (systemd timer).

  승인 N   apply pending approval N (a "key = value" change goes into params.json; anything else is recorded)
  거절 N   drop pending approval N
  상태     reply with the current rank and scores
  anything else   saved as a note in human-notes.jsonl; the strategist reads it on its next run

Only messages written by SLACK_OWNER (Dana's member ID) in SLACK_CHANNEL count; bot messages and edits are ignored.
Needs SLACK_BOT_TOKEN (xoxb-, scope groups:history or channels:history), SLACK_CHANNEL, SLACK_OWNER in runtime/strategist.env.
"""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import notify

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "logs/slack-inbox-state.json"
PENDING, RESOLVED = ROOT / "approvals/pending.jsonl", ROOT / "approvals/resolved.jsonl"


def env():
    out = {}
    path = ROOT / "runtime/strategist.env"
    for line in path.read_text().splitlines() if path.exists() else []:
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def read_jsonl(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def write_jsonl(path, rows):
    path.parent.mkdir(exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path, row):
    path.parent.mkdir(exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def history(token, channel, oldest):
    query = urllib.parse.urlencode({"channel": channel, "oldest": oldest, "limit": 50})
    request = urllib.request.Request("https://slack.com/api/conversations.history?" + query,
                                     headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "slack error"))
    return sorted(data.get("messages", []), key=lambda m: float(m["ts"]))


def set_param(key, value):
    path = ROOT / "params.json"
    params = json.loads(path.read_text())
    obj = params
    parts = key.split(".")
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    old = obj.get(parts[-1])
    obj[parts[-1]] = value
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(params, ensure_ascii=False, indent=1))
    tmp.replace(path)
    return old


def known_key(key):
    import sys
    sys.path.insert(0, str(ROOT))
    import shop_strategy
    section, _, rest = key.partition(".")
    node = shop_strategy.DEFAULTS.get(section)
    for part in rest.split(".") if rest else []:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return node is not None and rest != ""


def handle(text, now):
    pending = read_jsonl(PENDING)
    m = re.fullmatch(r"\s*(승인|거절|approve|reject)\s*(\d+)\s*", text, re.I)
    if m:
        verb, n = m.group(1).lower(), int(m.group(2))
        if not 1 <= n <= len(pending):
            return f"승인 대기 {n}번이 없어요. 지금 {len(pending)}건이 있어요."
        item = pending.pop(n - 1)
        write_jsonl(PENDING, pending)
        if verb in ("거절", "reject"):
            append_jsonl(RESOLVED, {**item, "resolved": now, "decision": "rejected by Dana in Slack"})
            append_jsonl(ROOT / "human-notes.jsonl", {"ts": now, "note": f"Rejected: {item['what']}"})
            return f"거절했어요: {item['what'][:150]}"
        change = re.fullmatch(r"([A-Za-z_.]+) = (.+)", item["what"].strip())
        if change and known_key(change.group(1)):
            try:
                value = json.loads(change.group(2))
            except ValueError:
                value = None
            if value is not None:
                old = set_param(change.group(1), value)
                append_jsonl(RESOLVED, {**item, "resolved": now, "decision": f"approved in Slack; applied {old} -> {value}"})
                append_jsonl(ROOT / "human-notes.jsonl", {"ts": now, "note": f"Approved and applied: {item['what']}"})
                return f"승인해서 바로 적용했어요: {change.group(1)} {old} → {value}"
        append_jsonl(RESOLVED, {**item, "resolved": now, "decision": "approved in Slack; needs a code change, waiting for Claude"})
        append_jsonl(ROOT / "human-notes.jsonl", {"ts": now, "note": f"Approved (needs code, not applied yet): {item['what']}"})
        return f"승인을 기록했어요. 설정값이 아니라 코드를 고쳐야 하는 일이라, 앱에서 Claude가 다음에 반영해요: {item['what'][:150]}"
    if re.fullmatch(r"\s*(상태|status)\s*", text, re.I):
        report = (ROOT / "logs/strategist-report.md").read_text(encoding="utf-8").strip().splitlines()
        return "\n".join([report[0]] + [l for l in report if l.startswith("순위")] + [f"승인 대기 {len(pending)}건"])
    append_jsonl(ROOT / "human-notes.jsonl", {"ts": now, "note": text[:1500], "via": "slack"})
    return "메모로 남겼어요. 전략가가 다음 실행(매시 07분)에 읽어요."


def main():
    e = env()
    token, channel, owner = e.get("SLACK_BOT_TOKEN"), e.get("SLACK_CHANNEL"), e.get("SLACK_OWNER")
    if not (token and channel and owner):
        return
    state = json.loads(STATE.read_text()) if STATE.exists() else {"oldest": str(time.time())}
    for msg in history(token, channel, state["oldest"]):
        state["oldest"] = msg["ts"]
        if msg.get("user") != owner or msg.get("bot_id") or msg.get("subtype"):
            continue
        now = time.strftime("%Y-%m-%d %H:%M KST")
        reply = handle(msg.get("text", ""), now)
        append_jsonl(ROOT / "logs/slack-inbox.jsonl", {"ts": now, "text": msg.get("text", "")[:500], "reply": reply})
        notify.send(reply, force=True)   # a reply to 다나's own message
    STATE.write_text(json.dumps(state))


if __name__ == "__main__":
    main()
