"""market.rules (design R1, 20-6 D-17): read the market's rules page and agent.md, keep a copy when they change,
and when they do, alert at once and mark every knowledge item resting on them for review.

    py -m harness.rules_watch          # one check (the meta-harness runner calls it every 30 min)

The rules page is HTML: compared as its text (tags, scripts and styles dropped, whitespace folded), so a changed
script or style does not count as a rule change. Copies: memory/market/rules/<doc>-<YYYYMMDD-HHMM>.txt (information,
never edited). The first check only keeps the copy.
"""
import difflib
import hashlib
import html
import json
import re
import sys
import time
import urllib.request

from core import atomic, paths
from harness import alert, knowledge

MARKET = "https://market.ourolabs.ai"
DOCS = {"rules": "/rules", "agent": "/agent.md"}
EVERY_S = 30 * 60


def text_of(body, is_html):
    if not is_html:
        return re.sub(r"[ \t]+\n", "\n", body).strip()
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr|section)>", "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return "\n".join(l for l in (re.sub(r"\s+", " ", x).strip() for x in body.splitlines()) if l)


def fetch(doc):
    req = urllib.request.Request(MARKET + DOCS[doc], headers={"User-Agent": "gom31-harness/rules-watch"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode("utf-8", "replace")
        return text_of(body, "html" in (r.headers.get("Content-Type") or ""))


def patch_of(text):
    """The newest patch number the page names (e.g. '3.3'), or None."""
    found = re.findall(r"(?i)patch(?: notes?)?\s*v?(\d+\.\d+)", text)
    return max(found, key=lambda v: tuple(map(int, v.split(".")))) if found else None


def _dir():
    d = paths.memory() / "market" / "rules"
    d.mkdir(parents=True, exist_ok=True)
    return d


def check(now=None, fetcher=fetch):
    """One check of every document. Returns {doc: {"changed": bool, ...}}."""
    now = now or time.time()
    state_p = _dir() / "state.json"
    state = atomic.read_json(state_p, default=None) or {}
    out = {}
    for doc in DOCS:
        try:
            text = fetcher(doc)
        except Exception as e:
            fails = state.get(doc, {}).get("fails", 0) + 1
            state.setdefault(doc, {})["fails"] = fails
            out[doc] = {"changed": False, "error": type(e).__name__, "fails": fails}
            if fails == 3:                                  # 1.5 h without the rules: someone should look
                alert.send(f"[경고] 시장 {doc} 문서를 세 번 연속 읽지 못함 ({type(e).__name__})", "warn", now)
            continue
        h = hashlib.sha256(text.encode()).hexdigest()[:16]
        prev = state.get(doc) or {}
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(now))
        if prev.get("hash") == h:
            state[doc] = {**prev, "checked": now, "fails": 0}
            out[doc] = {"changed": False, "hash": h}
            continue
        (_dir() / f"{doc}-{stamp}.txt").write_text(text, encoding="utf-8")
        state[doc] = {"hash": h, "file": f"{doc}-{stamp}.txt", "checked": now, "changed": now, "fails": 0,
                      "patch": patch_of(text)}
        if not prev.get("hash"):
            out[doc] = {"changed": False, "first": True, "hash": h, "patch": patch_of(text)}
            continue
        old = (_dir() / prev["file"]).read_text(encoding="utf-8").splitlines() if prev.get("file") else []
        diff = [l for l in difflib.unified_diff(old, text.splitlines(), lineterm="", n=0)
                if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        flagged = knowledge.flag_review("market:", f"market {doc} changed {stamp}", now)
        knowledge.write({"id": f"market:{doc}", "about": f"market:{doc}", "claim": f"the {doc} text as of {stamp}",
                         "source": f"market:{doc}@{h}", "method": "harness.rules_watch copy",
                         "links": [f"memory/market/rules/{doc}-{stamp}.txt"]}, now)
        alert.send(f"[긴급] 시장 {doc} 문서가 바뀜 (patch {prev.get('patch')} → {patch_of(text)}), "
                   f"{len(diff)}줄, 재검토 표시 {len(flagged)}건\n" + "\n".join(diff[:12]), "now", now)
        out[doc] = {"changed": True, "hash": h, "diff_lines": len(diff), "flagged": len(flagged),
                    "patch": patch_of(text)}
    atomic.write_json(state_p, state)
    return out


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=1))
