"""Retrieval over knowledge/market_primer.md (a small RAG): BM25 over the primer's sections, tags weighted.

The primer has about a dozen sections, so embeddings and a vector store would add infrastructure without better
recall; BM25 with the hand-written tags (English and Korean) finds the right principle for a query like
"overstock mattress" or "유통기한 할인". Agents call it as the read-only `primer` tool.
"""
import math
import re
from collections import Counter

from . import config

PATH = config.ROOT / "knowledge" / "market_primer.md"
TOKEN = re.compile(r"[a-z0-9]+|[가-힣]+")
TAG_WEIGHT = 3
_CACHE = {}


def sections(path=PATH):
    key = (str(path), path.stat().st_mtime)
    if key not in _CACHE:
        parts = re.split(r"(?m)^## ", path.read_text(encoding="utf-8"))[1:]
        out = []
        for p in parts:
            title, _, body = p.partition("\n")
            tags = re.search(r"(?m)^tags:(.*)$", body)
            tag_text = tags.group(1) if tags else ""
            toks = TOKEN.findall((title + " " + body).lower()) + TOKEN.findall(tag_text.lower()) * (TAG_WEIGHT - 1)
            out.append({"title": title.strip(), "text": ("## " + p).strip(), "tf": Counter(toks), "len": len(toks)})
        _CACHE.clear()
        _CACHE[key] = out
    return _CACHE[key]


def search(query, k=2, path=PATH):
    secs = sections(path)
    q = TOKEN.findall(str(query or "").lower())
    if not q or not secs:
        return []
    n, avg = len(secs), sum(s["len"] for s in secs) / len(secs)
    scored = []
    for s in secs:
        score = 0.0
        for t in set(q):
            df = sum(1 for x in secs if t in x["tf"])
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            tf = s["tf"][t]
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * s["len"] / avg))
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [{"title": s["title"], "score": round(sc, 2), "text": s["text"][:1500]} for sc, s in scored[:k]]


def tool():
    from .react import Tool
    return Tool("primer", "market principles (시장학개론) relevant to a question, with sources and how they apply "
                "to this market; e.g. 'overstock', 'warranty risk', 'expiry discount', 'when to post'",
                lambda query="", k=2: search(query, max(1, min(3, int(k)))), {"query": "str", "k": "int <= 3"})
