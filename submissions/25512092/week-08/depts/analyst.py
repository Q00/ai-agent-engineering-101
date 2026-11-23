"""Strategy analyst: proposes strategy changes; nothing is applied without the person's approval on Telegram.

Step 1 (this part): Telegram both ways.
  - send_proposal(): a message with three buttons (승인 approve / 거절 reject / 나중에 later), callback data
    "<action>:<proposal id>"
  - poll(): getUpdates long polling (25 s). Only callbacks and messages from TELEGRAM_CHAT_ID count; anything else
    is ignored and logged without its content. Each proposal is decided once: a second press, or a press after it
    expired, only gets "이미 처리됨".
  - The bot token and chat id come from the environment only (alerts._env), never written anywhere; errors are
    logged by type, never with the URL (it carries the token).
State: market.db tables proposals and analyst_meta (getUpdates offset). Log: logs/analyst.log.
"""
import json
import os
import time
from datetime import datetime

import alerts
import decisions
from market_db import connect

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(HERE, "logs", "analyst.log")
EXPIRE_S = 3 * 3600          # no answer in 3 h: the proposal expires and nothing changes
LATER_S = 3 * 3600           # "나중에": asked once more after 3 h
API = "https://api.telegram.org/bot{token}/{method}"


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {msg}\n")


def db():
    d = connect()
    d.execute("CREATE TABLE IF NOT EXISTS proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, "
              "key TEXT, value TEXT, reason TEXT, evidence TEXT, status TEXT, expires REAL, message_id INTEGER, "
              "decided_ts REAL, result TEXT, asked INTEGER DEFAULT 0)")
    d.execute("CREATE TABLE IF NOT EXISTS analyst_meta (k TEXT PRIMARY KEY, v TEXT)")
    return d


def _call(method, **params):
    """Telegram Bot API call; returns the result or None. Never logs the URL or the token."""
    token, chat = alerts._env("TELEGRAM_BOT_TOKEN"), alerts._env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return None
    import httpx
    try:
        r = httpx.post(API.format(token=token, method=method), json=params,
                       timeout=(params.get("timeout") or 0) + 15)
        j = r.json()
        if not j.get("ok"):
            log(f"telegram {method}: not ok ({j.get('error_code')}: {str(j.get('description'))[:80]})")
            return None
        return j.get("result")
    except Exception as e:
        log(f"telegram {method} failed: {type(e).__name__}")
        return None


def chat_id():
    c = alerts._env("TELEGRAM_CHAT_ID")
    return int(c) if c and c.lstrip("-").isdigit() else None


BUTTONS = lambda pid: {"inline_keyboard": [[{"text": "승인", "callback_data": f"approve:{pid}"},
                                              {"text": "거절", "callback_data": f"reject:{pid}"},
                                              {"text": "나중에", "callback_data": f"later:{pid}"}]]}


def send_proposal(kind, key, value, reason, evidence="", expire_s=EXPIRE_S):
    """Store a proposal and send it with buttons. Returns its id."""
    d = db()
    try:
        now = time.time()
        cur = d.execute("INSERT INTO proposals (ts, kind, key, value, reason, evidence, status, expires) "
                        "VALUES (?,?,?,?,?,?,?,?)", (now, kind, key, json.dumps(value), reason, evidence,
                                                     "sent", now + expire_s))
        pid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    text = (f"[제안 #{pid}] {key} -> {value}\n{reason}" + (f"\n근거: {evidence}" if evidence else "") +
            f"\n{EXPIRE_S // 3600}시간 안에 응답이 없으면 변경 없음")
    res = _call("sendMessage", chat_id=chat_id(), text=text[:4000], reply_markup=BUTTONS(pid))
    d = db()
    try:
        d.execute("UPDATE proposals SET message_id=?, asked=asked+1 WHERE id=?", ((res or {}).get("message_id"), pid))
        d.commit()
    finally:
        d.close()
    log(f"proposal #{pid} sent ({kind} {key} -> {value}): telegram {'ok' if res else 'failed'}")
    decisions.record("analyst", "proposal", key, value, f"#{pid}: {reason}")
    return pid


HANDLERS = {}        # kind -> function(proposal row dict) -> result text; steps 3-4 register appliers here
REVERT = None        # function(proposal row dict) -> result text (depts/analyst_apply.revert)
REAPPLY = None       # function(proposal row dict) -> result text (depts/analyst_apply.reapply)
UNDO_BUTTON = lambda pid: {"inline_keyboard": [[{"text": "되돌리기", "callback_data": f"revert:{pid}"}]]}


def _finish(p, status, result, cq_id):
    d = db()
    try:
        d.execute("UPDATE proposals SET status=?, decided_ts=?, result=? WHERE id=?", (status, time.time(), result, p["id"]))
        d.commit()
    finally:
        d.close()
    label = {"approved": "승인됨", "rejected": "거절됨", "failed": "승인했지만 반영 실패", "reverted": "되돌림"}[status]
    _call("answerCallbackQuery", callback_query_id=cq_id, text=label)
    if p.get("message_id"):
        extra = {"reply_markup": UNDO_BUTTON(p["id"])} if status == "approved" and p["kind"] in ("policy", "limit") else {}
        _call("editMessageText", chat_id=chat_id(), message_id=p["message_id"],
              text=(f"[제안 #{p['id']}] {p['key']} -> {json.loads(p['value'])}\n{p['reason']}\n"
                    f"=> {label} {datetime.now():%m-%d %H:%M}" + (f": {result}" if result else ""))[:4000], **extra)
    decisions.record("analyst", status, p["key"], json.loads(p["value"]), f"#{p['id']}: {result or label}")
    log(f"proposal #{p['id']} {status}: {result}")


CONFIRM_FIRST = ("revert", "undo")
CONFIRM_BUTTONS = lambda action, pid: {"inline_keyboard": [[{"text": "정말 되돌리기", "callback_data": f"{action}_ok:{pid}"},
                                                            {"text": "취소", "callback_data": f"cancel_undo:{pid}"}]]}


def ask_confirm(action, pid, cq):
    """The first 되돌리기 press: one confirmation message (reply to the button's message); nothing changes yet."""
    _call("answerCallbackQuery", callback_query_id=cq["id"], text="한 번 더 확인해 주세요")
    mid = (cq.get("message") or {}).get("message_id")
    _call("sendMessage", chat_id=chat_id(), text=f"#{pid} 정말 되돌릴까요?", reply_markup=CONFIRM_BUTTONS(action, pid),
          **({"reply_to_message_id": mid} if mid else {}))
    decisions.record("person", "revert_ask", pid, 0, f"{action}: 되돌리기 눌림, 확인 요청")
    log(f"#{pid} {action}: confirmation asked")


def _close_confirm(cq, text):
    """The confirmation message: its buttons removed, the outcome written (each confirmation works once)."""
    m = cq.get("message") or {}
    if m.get("message_id"):
        _call("editMessageText", chat_id=chat_id(), message_id=m["message_id"], text=f"{m.get('text') or ''}\n{text}"[:4000])


def handle_callback(cq):
    """One button press. Only from TELEGRAM_CHAT_ID; each proposal once."""
    msg_chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
    who = (cq.get("from") or {}).get("id")
    if chat_id() is None or msg_chat != chat_id() or who != chat_id():
        log(f"callback ignored: not from the configured chat (from {who})")
        _call("answerCallbackQuery", callback_query_id=cq["id"], text="권한 없음")
        return "ignored"
    action, _, pid = (cq.get("data") or "").partition(":")
    # 11/09 ~23:40 (person): a 되돌리기 press (revert: approved proposal, undo: automatic policy change) only asks
    # "정말 되돌릴까요?" with [정말 되돌리기] / [취소]; the confirm button runs it
    if action in CONFIRM_FIRST:
        ask_confirm(action, pid, cq)
        return "confirm asked"
    if action == "cancel_undo":
        _call("answerCallbackQuery", callback_query_id=cq["id"], text="취소됨, 변경 없음")
        _close_confirm(cq, "=> 취소, 변경 없음")
        decisions.record("person", "revert_cancel", pid, 0, "되돌리기 확인에서 취소")
        return "cancelled"
    if action.endswith("_ok") and action[:-3] in CONFIRM_FIRST:
        action = action[:-3]
        _close_confirm(cq, "=> 되돌리기 진행")
    d = db()
    d.row_factory = __import__("sqlite3").Row
    try:
        row = d.execute("SELECT * FROM proposals WHERE id=?", (int(pid) if pid.isdigit() else -1,)).fetchone()
    finally:
        d.close()
    if row is None:
        _call("answerCallbackQuery", callback_query_id=cq["id"], text="없는 제안")
        return "unknown"
    p = dict(row)
    if action in ("undo", "pin") and p["kind"] == "policy_change":   # 11/07: automatic policy change messages
        from depts import policy_notify
        return policy_notify.on_button(action, p, cq["id"])
    if action == "reapply":                        # 다시 적용 after an automatic revert (check or CEO guard)
        if p["status"] != "auto_reverted" or REAPPLY is None:
            _call("answerCallbackQuery", callback_query_id=cq["id"], text="다시 적용할 수 없음")
            return "no reapply"
        try:
            _finish(p, "approved", "다시 적용: " + REAPPLY(p), cq["id"])
        except Exception as e:
            _call("answerCallbackQuery", callback_query_id=cq["id"], text="다시 적용 실패")
            alerts.send("urgent", f"analyst_reapply:{p['id']}", f"#{p['id']} 다시 적용 실패: {type(e).__name__}: {str(e)[:120]}")
        return "reapply"
    if action == "revert":                         # the 되돌리기 button on an applied approval
        if p["status"] != "approved" or REVERT is None:
            _call("answerCallbackQuery", callback_query_id=cq["id"], text="되돌릴 수 없음")
            return "no revert"
        try:
            _finish(p, "reverted", REVERT(p), cq["id"])
        except Exception as e:
            _call("answerCallbackQuery", callback_query_id=cq["id"], text="되돌리기 실패")
            alerts.send("urgent", f"analyst_revert:{p['id']}", f"#{p['id']} 되돌리기 실패: {type(e).__name__}: {str(e)[:120]}")
        return "revert"
    if p["status"] != "sent" or time.time() > p["expires"]:
        _call("answerCallbackQuery", callback_query_id=cq["id"], text="이미 처리됨")
        return "done already"
    if action == "approve":
        try:
            result = HANDLERS.get(p["kind"], lambda p: "기록만 (반영할 내용 없음)")(p)
            _finish(p, "approved", result, cq["id"])
        except Exception as e:
            _finish(p, "failed", f"{type(e).__name__}: {str(e)[:150]}", cq["id"])
            alerts.send("urgent", f"analyst_apply:{p['id']}", f"승인 #{p['id']} 반영 실패: {type(e).__name__}")
    elif action == "reject":
        _finish(p, "rejected", "", cq["id"])
    elif action == "later":
        if p["asked"] >= 2:                        # "later" only once: the second time it closes, no change
            _finish(p, "rejected", "두 번째 '나중에': 변경 없음", cq["id"])
        else:
            d = db()
            try:                                   # re-ask at expires; then it gets a fresh EXPIRE_S
                d.execute("UPDATE proposals SET status='later', expires=? WHERE id=?", (time.time() + LATER_S, p["id"]))
                d.commit()
            finally:
                d.close()
            _call("answerCallbackQuery", callback_query_id=cq["id"], text=f"{LATER_S // 3600}시간 뒤 다시 물어볼게요")
            if p.get("message_id"):
                _call("editMessageText", chat_id=chat_id(), message_id=p["message_id"],
                      text=f"[제안 #{p['id']}] {p['key']} -> {json.loads(p['value'])}\n=> 나중에 ({LATER_S // 3600}시간 뒤 다시)")
            decisions.record("analyst", "later", p["key"], json.loads(p["value"]), f"#{p['id']}")
            log(f"proposal #{p['id']} later")
    return action


def poll(timeout=25):
    """One getUpdates round. Returns the number of updates handled."""
    d = db()
    try:
        off = d.execute("SELECT v FROM analyst_meta WHERE k='offset'").fetchone()
    finally:
        d.close()
    ups = _call("getUpdates", offset=int(off[0]) if off else None, timeout=timeout,
                allowed_updates=["callback_query", "message"]) or []
    for u in ups:
        try:
            if "callback_query" in u:
                handle_callback(u["callback_query"])
            elif "message" in u:
                c = ((u["message"].get("chat") or {}).get("id"))
                log(f"message ignored ({'my chat' if c == chat_id() else 'other chat'}): text commands are not used")
        except Exception as e:
            log(f"update failed: {type(e).__name__}")
        d = db()
        try:
            d.execute("INSERT OR REPLACE INTO analyst_meta VALUES ('offset', ?)", (str(u["update_id"] + 1),))
            d.commit()
        finally:
            d.close()
    return len(ups)


def housekeeping():
    """Re-ask "나중에" proposals once when their wait is over; expire unanswered ones (no change)."""
    now = time.time()
    d = db()
    d.row_factory = __import__("sqlite3").Row
    try:
        again = d.execute("SELECT * FROM proposals WHERE status='later' AND expires<?", (now,)).fetchall()
        gone = d.execute("SELECT * FROM proposals WHERE status='sent' AND expires<?", (now,)).fetchall()
        for p in gone:
            d.execute("UPDATE proposals SET status='expired', decided_ts=? WHERE id=?", (now, p["id"]))
        d.commit()
    finally:
        d.close()
    for p in gone:
        decisions.record("analyst", "expired", p["key"], json.loads(p["value"]), f"#{p['id']}: no answer, no change")
        if p["message_id"]:
            _call("editMessageText", chat_id=chat_id(), message_id=p["message_id"],
                  text=f"[제안 #{p['id']}] {p['key']} -> {json.loads(p['value'])}\n=> 응답 없음, 변경 없음")
    for p in again:
        res = _call("sendMessage", chat_id=chat_id(), reply_markup=BUTTONS(p["id"]),
                    text=f"[제안 #{p['id']} 다시] {p['key']} -> {json.loads(p['value'])}\n{p['reason']}"[:4000])
        d = db()
        try:
            d.execute("UPDATE proposals SET status='sent', asked=2, expires=?, message_id=? WHERE id=?",
                      (now + EXPIRE_S, (res or {}).get("message_id"), p["id"]))
            d.commit()
        finally:
            d.close()
        log(f"proposal #{p['id']} asked again")
