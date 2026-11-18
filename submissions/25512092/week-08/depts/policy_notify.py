"""A Telegram message for every automatic policy change, with [되돌리기] and [24시간 고정] (person's decision 11/07
~16:15). Changes that did not change a value are only logged (decisions.jsonl), never sent.

Sources (each calls notify() right after its change is written):
  ceo      depts/ceo_policy.tick, live mode, every RUN_EVERY_S        policy.json > purchasing.<key>
  guard    depts/ceo_policy.guard, revert of the CEO keys            policy.json > purchasing.<key>
  qc       depts/qc.daily, midnight buying-split rule                QC 정책 테이블 (market.db policy) > <key>
  analyst  depts/analyst_apply._auto_revert, 3 h check revert        limits.py > <KEY> or policy.json > <key>

Each message is a row of market.db proposals (kind 'policy_change', value = the changes), so the buttons work across
restarts; depts/analyst.handle_callback hands 'undo' / 'pin' presses here.
  되돌리기    the previous value again, set as the person (policy.json write, a QC table row, or the analyst's
              re-apply for a reverted approval); the message is edited to say so
  24시간 고정 policy_pins.json {key: until}: pinned(key) is True for 24 h and every automatic changer above skips that
              key (the person's own changes, approvals included, are not blocked)
"""
import json
import os
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINS = os.path.join(HERE, "policy_pins.json")
PIN_S = 24 * 3600
WHO = {"ceo": "CEO (30분 판단)", "guard": "CEO 가드 되돌리기", "qc": "QC 자정 규칙", "analyst": "분석관 3시간 점검 되돌리기"}
BUTTONS = lambda nid: {"inline_keyboard": [[{"text": "되돌리기", "callback_data": f"undo:{nid}"},
                                              {"text": "24시간 고정", "callback_data": f"pin:{nid}"}]]}


# ---------------------------------------------------------------- pins
def _pins():
    try:
        return json.load(open(PINS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pinned(key, now=None):
    """True while `key` (e.g. 'purchasing.spend_share', 'limits.STOCK_CAP') is pinned against automatic changes."""
    return (now or time.time()) < float(_pins().get(key, 0))


def pin(key, now=None):
    now = now or time.time()
    p = {k: v for k, v in _pins().items() if float(v) > now}
    p[key] = now + PIN_S
    tmp = PINS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f)
    os.replace(tmp, PINS)
    return p[key]


# ---------------------------------------------------------------- the message
def _fmt(v):
    if v is None:
        return "없음 (코드 기본 규칙)"
    if isinstance(v, float) and v < 1:
        return f"{v:g}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def notify(source, changes, reason, evidence="", version=None, next_at=None, extra=None, log=print):
    """changes: [{"key": "purchasing.spend_share", "old": 0.4, "new": 0.5, "where": "policy.json > purchasing..."}].
    Only changes with old != new are sent. Returns the message id in proposals, or None."""
    changes = [c for c in changes if c.get("old") != c.get("new")]
    if not changes:
        return None
    import decisions
    from depts import analyst
    now = time.time()
    lines = [f"[정책 자동 변경] {WHO.get(source, source)} {datetime.fromtimestamp(now):%m-%d %H:%M:%S}"]
    if version:
        lines.append(f"정책 버전: {version[0]} -> {version[1]}")
    for c in changes:
        lines.append(f"- {c['where']}: {_fmt(c['old'])} -> {_fmt(c['new'])}")
    lines.append(f"이유: {reason}")
    if evidence:
        lines.append(f"근거: {evidence}")
    if next_at:
        lines.append(f"다음 판단: {next_at if isinstance(next_at, str) else datetime.fromtimestamp(next_at).strftime('%m-%d %H:%M')}")
    lines.append("[되돌리기] 직전 값으로 / [24시간 고정] 이 키를 24시간 자동 변경 금지 (직접 변경은 가능)")
    text = "\n".join(lines)
    value = {"source": source, "changes": changes, "extra": extra or {}}
    d = analyst.db()
    try:
        cur = d.execute("INSERT INTO proposals (ts, kind, key, value, reason, evidence, status, expires) VALUES "
                        "(?,?,?,?,?,?,?,?)", (now, "policy_change", ",".join(c["key"] for c in changes),
                                              json.dumps(value, ensure_ascii=False), reason[:500], evidence[:500],
                                              "notified", now + 30 * 86400))
        nid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    res = analyst._call("sendMessage", chat_id=analyst.chat_id(), text=f"#{nid} " + text[:3900], reply_markup=BUTTONS(nid))
    d = analyst.db()
    try:
        d.execute("UPDATE proposals SET message_id=? WHERE id=?", ((res or {}).get("message_id"), nid))
        d.commit()
    finally:
        d.close()
    decisions.record("qc", "policy_change_notice", ",".join(c["key"] for c in changes), [c["new"] for c in changes],
                     f"#{nid} {source}: {reason[:200]}; telegram {'ok' if res else 'failed'}")
    log(f"[policy_notify] #{nid} {source} {[(c['key'], c['old'], c['new']) for c in changes]}: "
        f"telegram {'ok' if res else 'failed'}")
    return nid


# ---------------------------------------------------------------- the buttons (analyst process)
def _undo(v):
    """Set every change back to its old value, as the person. Returns a result text."""
    import policy
    out = []
    if v["source"] == "analyst":                    # a reverted approval: apply the approved value again
        from depts import analyst, analyst_apply
        d = analyst.db()
        d.row_factory = __import__("sqlite3").Row
        try:
            p = dict(d.execute("SELECT * FROM proposals WHERE id=?", (v["extra"]["pid"],)).fetchone())
        finally:
            d.close()
        text = analyst_apply.reapply(p)
        return f"다시 적용: {text}"
    if v["source"] == "qc":
        from depts import qc
        from market_db import connect
        db = connect()
        try:
            cur = qc.policy(db)
            for c in v["changes"]:
                cur[c["key"].split(".", 1)[1]] = c["old"]
            db.execute("INSERT INTO policy VALUES (?,?,?,?)", (time.time(), cur["high_share"], cur["high_max"],
                                                               "person: 되돌리기 of the QC midnight rule"))
            db.commit()
        finally:
            db.close()
        return f"QC 정책 테이블: high_share {cur['high_share']}, high_max {cur['high_max']}"
    for c in v["changes"]:                          # ceo / guard: policy.json
        sec, k = c["key"].split(".", 1)
        new = policy.revert_keys(sec, {k: c["old"]}, f"person: 되돌리기 {c['key']} -> {c['old']}", by="person")
        out.append(f"{c['key']} -> {_fmt(c['old'])} (policy v{new['version']})")
    return "; ".join(out)


def on_button(action, p, cq_id):
    """'undo' or 'pin' on a policy_change message. Only once per action."""
    import decisions
    from depts import analyst
    v = json.loads(p["value"])
    done = json.loads(p.get("result") or "{}") if (p.get("result") or "").startswith("{") else {}
    if done.get(action):
        analyst._call("answerCallbackQuery", callback_query_id=cq_id, text="이미 처리됨")
        return "done already"
    try:
        if action == "undo":
            res = _undo(v)
            label = "되돌림"
        else:
            until = max(pin(c["key"]) for c in v["changes"])
            res = f"{', '.join(c['key'] for c in v['changes'])} 고정, {datetime.fromtimestamp(until):%m-%d %H:%M}까지"
            label = "24시간 고정"
    except Exception as e:
        analyst._call("answerCallbackQuery", callback_query_id=cq_id, text="실패")
        __import__("alerts").send("urgent", f"policy_button:{p['id']}", f"#{p['id']} {action} 실패: "
                                  f"{type(e).__name__}: {str(e)[:150]}")
        return "failed"
    done[action] = f"{datetime.now():%m-%d %H:%M} {res}"
    d = analyst.db()
    try:
        d.execute("UPDATE proposals SET result=? WHERE id=?", (json.dumps(done, ensure_ascii=False), p["id"]))
        d.commit()
    finally:
        d.close()
    analyst._call("answerCallbackQuery", callback_query_id=cq_id, text=label)
    if p.get("message_id"):
        analyst._call("editMessageReplyMarkup", chat_id=analyst.chat_id(), message_id=p["message_id"],
                      reply_markup=BUTTONS(p["id"]))
        analyst._call("sendMessage", chat_id=analyst.chat_id(), reply_to_message_id=p["message_id"],
                      text=f"#{p['id']} {label}: {res}"[:4000])
    decisions.record("person", f"policy_{action}", p["key"], res, f"#{p['id']} ({v['source']})")
    analyst.log(f"policy change #{p['id']} {action}: {res}")
    return action
