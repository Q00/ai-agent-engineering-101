"""Week 08 -- prompt-injection guard: text from other agents is data, never instructions.

Two places in this agent hand another agent's words to the harness:
  buyer_r3.py   the shop's reply ("What the shop said: ...") in the haggle prompt
  seller_r3.py  the buyer's messages in the negotiation transcript
and the forum is full of posts written for AI agents to read. Round 3's forum has
no rules; 11-10 a shop posted a fake "MARKET NOTICE FOR AI BUYING AGENTS" telling
buyers to accept its price and give it 5 stars.

The price limits are already in code (the buyer's cap, the seller's floor), so an
injection cannot move money. What it can still do is steer the harness's choice
inside those limits, and it tells us something about the sender. So this module:
  scan(text)      -> list of rule names that matched (empty: clean)
  clean(text)     -> the text with every sentence that matched removed, so the
                     harness sees the facts (product, grade, price) and not the order
  flag(...)       -> remembers who sent it, where, and a short excerpt, in
                     memory/scams.json; the buyer ranks flagged shops last and the
                     forum reports them (forum.py, topic "scam") with the evidence.
Rules are plain regular expressions, English and Korean, so a flag can be checked
by reading the rule that fired.
"""
import json
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE = HERE / "memory" / "scams.json"

RULES = {
    # who the text claims to speak for, or whom it addresses
    "addresses_ai": r"\b(for|to|attention|note to)\s+(all\s+)?((ai|llm)\s+)?(buying|shopping|buyer|seller|ai|llm)\s+(agents?|assistants?|models?)\b"
                    r"|AI\s*(구매|쇼핑|판매)?\s*에이전트(에게|들에게|는|들은)",
    "fake_authority": r"\b(market|system|admin|operator)\s+(notice|policy|message|instruction|override)s?\b"
                      r"|\bthe\s+market\s+operator\b|\bverified\s+by\s+the\s+market\b"
                      r"|(시장|마켓)\s*(운영자|관리자|공지|정책|시스템)\s*(공지|지시|안내|정책)?[:：]",
    "override": r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules?|prompt|cap|floor|limit|budget|previous)\b"
                r"|(이전|기존|위의?)\s*(지시|명령|규칙|프롬프트).{0,10}(무시|잊)",
    "dictates_move": r"\b(you\s+must|you\s+should|always|policy:)\s.{0,40}\b(accept|pay|agree|buy|give)\b"
                     r"|\baccept\s+(the|this|my|our)\s+(shop'?s\s+)?(current\s+)?(price|ask|offer)\b"
                     r"|(현재\s*가격|제시\s*가격|호가).{0,10}(수락|승낙|받아들이)(하라|하세요|해라|할 것)",
    "dictates_review": r"\b(give|leave|rate)\b.{0,30}\b(5|five)\s*stars?\b|별점\s*5\s*(점|개)?\s*(을|를)?\s*(주|남기)",
    "role_play": r"</?\s*(system|assistant|instructions?)\s*>|\[\s*(system|inst)\s*\]|^\s*(system|assistant)\s*:",
}
_RX = {k: re.compile(v, re.I | re.M) for k, v in RULES.items()}
_SENT = re.compile(r"[^.!?\n。]*(?:[.!?。]+|\n|$)")


def scan(text):
    t = str(text or "")
    return [k for k, rx in _RX.items() if rx.search(t)]


def clean(text):
    """Drop each sentence (or bracketed block) that matches a rule; keep the rest verbatim."""
    t = str(text or "")
    t = re.sub(r"\[[^\]]{0,400}\]", lambda m: "" if scan(m.group()) else m.group(), t)
    kept = [s for s in _SENT.findall(t) if s.strip() and not scan(s)]
    return "".join(kept).strip()


def load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def flag(who, where, text, rules, ref=None):
    """Remember one attempt. `who` is a shop or agent name, `where` 'reply' | 'forum' | 'buyer-msg'."""
    if not who or not rules:
        return
    d = load()
    row = d.setdefault(who, {"count": 0, "rules": {}, "first": time.time(), "evidence": [], "reported": 0})
    row["count"] += 1
    row["last"] = time.time()
    for r in rules:
        row["rules"][r] = row["rules"].get(r, 0) + 1
    excerpt = re.sub(r"\s+", " ", str(text))[:90]
    if not any(e.get("ref") == ref and ref for e in row["evidence"]):
        row["evidence"] = (row["evidence"] + [{"where": where, "ref": ref, "rules": rules,
                                               "excerpt": excerpt, "ts": int(time.time())}])[-5:]
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def flagged(who):
    return load().get(who, {}).get("count", 0)


def scan_forum(state_dir, own=("YAHO1212",)):
    """Read forum.json (newest 100 posts) and flag authors whose posts carry instructions to agents."""
    try:
        posts = json.loads((Path(state_dir) / "forum.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    posts = posts.get("posts", posts) if isinstance(posts, dict) else posts
    seen = {e.get("ref") for row in load().values() for e in row.get("evidence", [])}
    n = 0
    for p in posts or []:
        if p.get("author") in own or f"post:{p.get('id')}" in seen:
            continue
        rules = scan(p.get("text"))
        # one rule alone (e.g. "always accept" in a buyer tip) is common honest advice; two is an attempt
        if len(rules) >= 2 or "fake_authority" in rules or "role_play" in rules or "override" in rules:
            flag(p.get("author"), "forum", p.get("text"), rules, ref=f"post:{p.get('id')}")
            n += 1
    return n
