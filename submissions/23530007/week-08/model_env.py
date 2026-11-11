"""How the agents' `claude -p` calls authenticate (user 11-08: use my API key).

The key lives outside git, in ~/.config/ai-agent-market/anthropic_api_key (mode 600); it is never read into
logs or commits. When the file is there a call runs with ANTHROPIC_API_KEY set from it; if that call fails
(non-zero exit or no answer, e.g. the key was revoked or hit its limit) it runs once more on the CLI login,
which is what every call used before. Without the file nothing changes.
"""
import os
import subprocess
from pathlib import Path

KEY_FILE = Path.home() / ".config" / "ai-agent-market" / "anthropic_api_key"


def env(use_key=True):
    e = dict(os.environ)
    if use_key and KEY_FILE.is_file():
        key = KEY_FILE.read_text().strip()
        if key:
            e["ANTHROPIC_API_KEY"] = key
    return e


SPEND = Path(__file__).resolve().parent / ".market" / "spend.jsonl"
CREDIT_ANCHOR = (1794372842.0, 173.0)        # 11-11 ~04:55 UTC: ~$181 at 02:20 (ops_cost estimate) minus ~$8 spent since


def _model_of(cmd):
    try:
        return cmd[cmd.index("--model") + 1]
    except (ValueError, IndexError):
        return "?"


def _unwrap(p, cmd, prompt, secs, role, fell_back):
    """11-11 (user: the API bill): every claude -p call runs with --output-format json; the JSON carries the
    answer in 'result' plus total_cost_usd and token counts. Hand callers the plain answer in p.stdout (as before)
    and append one row per call to .market/spend.jsonl so cost per role is measured, not guessed."""
    import json
    import time
    raw = (p.stdout or "").strip()
    try:
        j = json.loads(raw)
    except ValueError:
        j = None
    if not isinstance(j, dict) or "result" not in j:
        return p
    u = j.get("usage") or {}
    mu = j.get("modelUsage") or {}
    main_model = max(mu, key=lambda k: (mu[k] or {}).get("costUSD", 0)) if mu else _model_of(cmd)   # the CLI also bills a small haiku helper
    row = {"t": round(time.time(), 1), "role": role or "?", "model": main_model, "secs": round(secs, 1),
           "cost": round(float(j.get("total_cost_usd") or 0), 5), "chars_in": len(prompt),
           "input": u.get("input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
           "cache_write": u.get("cache_creation_input_tokens", 0), "output": u.get("output_tokens", 0),
           "thinking": (u.get("output_tokens_details") or {}).get("thinking_tokens", 0),
           "is_error": bool(j.get("is_error")), "subtype": j.get("subtype"), "fallback": fell_back}
    try:
        SPEND.parent.mkdir(exist_ok=True)
        with SPEND.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    p.spend = row
    if j.get("is_error") or p.returncode != 0:
        row["is_error"] = True
        p.stdout = ""                          # the 'result' is an error text (e.g. budget exceeded), not an answer
        p.returncode = p.returncode or 1
        p.error_text = str(j.get("result"))[:300]
    else:
        p.stdout = str(j.get("result") or "")
    return p


def run(cmd, prompt, cwd, timeout, role=None):
    """subprocess.run of cmd with the key; once more on the CLI login if that fails. Timeouts propagate.
    Adds --output-format json when the caller did not, and records the call in spend.jsonl (see _unwrap)."""
    import time
    cmd = list(cmd)
    if "--output-format" not in cmd:
        cmd += ["--output-format", "json"]
    t0 = time.time()
    p = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env())
    fell_back = False
    # 11-11: a call stopped by --max-budget-usd (or any other error the CLI reports as JSON) exits 1 with a JSON
    # body; that is not a key failure, so it is recorded and returned, never re-run on the login
    if p.stdout.strip().startswith("{") and '"result"' in p.stdout:
        return _unwrap(p, cmd, prompt, time.time() - t0, role, False)
    if (p.returncode != 0 or not p.stdout.strip()) and KEY_FILE.is_file():
        fell_back = True
        err = (p.stderr or "")[-200:].replace("\n", " ")
        p = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=env(use_key=False))
        try:
            alert_user("ops", [f"API 키 호출 실패 → 구독 로그인으로 대체 실행 ({role or '?'}, rc {p.returncode}): {err or '출력 없음'}"])
        except Exception:
            pass
    return _unwrap(p, cmd, prompt, time.time() - t0, role, fell_back)


def spend_rows(since):
    """spend.jsonl rows with t >= since."""
    import json
    out = []
    try:
        for line in SPEND.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("t", 0) >= since:
                out.append(r)
    except OSError:
        pass
    return out


def spend_summary(now=None):
    """{'today': $ in the last 24 h, 'per_role': {...}, 'credit_left': estimate from CREDIT_ANCHOR}."""
    import time
    now = now or time.time()
    day = spend_rows(now - 86400)
    per = {}
    for r in day:
        per[r["role"]] = per.get(r["role"], 0) + r["cost"]
    all_rows = spend_rows(CREDIT_ANCHOR[0])
    return {"today": round(sum(r["cost"] for r in day), 2), "calls": len(day),
            "per_role": {k: round(v, 2) for k, v in sorted(per.items(), key=lambda kv: -kv[1])},
            "credit_left": round(CREDIT_ANCHOR[1] - sum(r["cost"] for r in all_rows), 1)}


ALERTS = Path(__file__).resolve().parent / ".market" / "agent_alerts.json"


def alert_user(role, items, min_gap=1800):
    """Push an agent's 'alert_user' items to the user (user 11-08: 'why did I find this, not the agent?' - the
    agents found it but nothing told the user). At most one push per role per min_gap seconds, and an alert whose
    text was pushed in the last 6 h is not pushed again. Returns what was sent."""
    import json
    import subprocess
    import sys
    import time
    items = [str(x).strip() for x in (items or []) if str(x).strip()][:3]
    if not items:
        return []
    try:
        st = json.loads(ALERTS.read_text())
    except (OSError, ValueError):
        st = {}
    now = time.time()
    sent = {k: t for k, t in st.get("sent", {}).items() if now - t < 6 * 3600}
    fresh = [x for x in items if x[:120] not in sent]
    if not fresh or now - st.get("last", {}).get(role, 0) < min_gap:
        return []
    name = {"shop": "상점 에이전트", "buyer": "구매 에이전트", "curator": "큐레이터", "ops": "운영", "guard": "가드"}.get(role, role)
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "notify.py"),
                    f"[yms979] {name} 경보", "\n\n".join(fresh)], capture_output=True, text=True)
    for x in fresh:
        sent[x[:120]] = now
    st["sent"], st.setdefault("last", {})[role] = sent, now
    ALERTS.write_text(json.dumps(st, ensure_ascii=False))
    return fresh
