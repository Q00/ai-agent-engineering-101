"""L3 of the memory (HARNESS.md 2.4): finding the right written record, and following it down to the raw lines.

memory.search looks through what the outer runtime wrote (L2): slice summaries, participant profiles and their
history, briefs, and the user's decisions. Ideas from LongMemEval (Wu et al., 2024) shape it:

- keys on every record: kind, time, slice, strategy version, the participants and SKUs it names, so a question
  about one participant or one stretch of time is filtered by key first, then ranked by words;
- time first: since/until and version filters cut before any ranking;
- knowledge updates: a profile's newest version is the record; older versions come back only with
  history=true, marked superseded;
- abstention: when nothing matches, it says so ("no record"), so a reader does not fill the gap with a guess.

Ranking is BM25 over words (and two-character pieces of Korean words), standard library only; a few hundred
records a round keep it fast. Counts never come from here: those are eval.* (L1).

memory.raw follows a record's evidence down to the raw lines (L0), a few at a time.
"""
import json
import math
import re

from core import atomic, paths
from tools.registry import Refused, tool

SKU = re.compile(r"\bG[0-9a-f]{8}\b")
WORD = re.compile(r"\w+", re.U)
HANGUL = re.compile(r"[가-힣]")
K1, B = 1.5, 0.75
MIN_COVER = 0.5            # a record must hold at least half of the query's content words, else it is not an answer
STOP = set("a an the of on in at to for from by with and or is are was were be do did does what which who whom when "
           "where why how our we us you it its this that these those any some about there".split())


def tokens(text):
    out = []
    for w in WORD.findall((text or "").lower()):
        out.append(w)
        if HANGUL.search(w) and len(w) > 2:
            out += [w[i:i + 2] for i in range(len(w) - 1)]
    return out


def _text(*parts):
    return " ".join(p if isinstance(p, str) else json.dumps(p, ensure_ascii=False, default=str) for p in parts if p)


def _claims(items):
    return " ".join(f"{x.get('claim', '')} ({', '.join(x.get('evidence') or [])})" for x in items or [])


def records():
    """Every L2 record with its keys."""
    m, out = paths.memory(), []
    for f in sorted((m / "epochs").glob("*/*/*.json")):
        d = atomic.read_json(f, default=None) or {}
        out.append({"kind": f"{d.get('kind', f.stem)} summary", "ts": d.get("ts"), "slice": d.get("slice"),
                    "epoch": d.get("epoch"), "text": _text(d.get("summary"), _claims(d.get("findings"))),
                    "pointer": f.relative_to(m).as_posix()})
    for f in sorted((m / "profiles").glob("*.json")):
        d = atomic.read_json(f, default=None) or {}
        out.append(_profile(d, f.relative_to(m).as_posix(), superseded=False))
        h = m / "profiles" / f"{d.get('name', f.stem)}.history.jsonl"
        if h.exists():
            for i, line in enumerate(h.read_text(encoding="utf-8").splitlines()):
                try:
                    out.append(_profile(json.loads(line), f"{h.relative_to(m).as_posix()}#{i}", superseded=True))
                except ValueError:
                    continue
    for f in sorted((m / "strategy" / "proposals").glob("*.json")):
        d = atomic.read_json(f, default=None) or {}
        out.append({"kind": "brief", "ts": d.get("ts"), "slice": d.get("slice"), "version": d.get("version_from"),
                    "text": _text(d.get("score"), _claims(d.get("happened")), d.get("hypothesis"), d.get("counterparts"),
                                  " ".join(f"{o['id']}: {o.get('expected')} {o.get('risk')} {json.dumps(o.get('change'), ensure_ascii=False)}"
                                           for o in d.get("options") or []),
                                  (d.get("audit") or {}).get("points")),
                    "pointer": f.relative_to(m).as_posix()})
    dec = paths.root() / "logs" / "strategy" / "decisions.jsonl"
    if dec.exists():
        for i, line in enumerate(dec.read_text(encoding="utf-8").splitlines()):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            out.append({"kind": "decision", "ts": d.get("ts"), "slice": d.get("proposal_id"), "version": d.get("installed"),
                        "text": _text(f"decision on brief {d.get('proposal_id')}: {d.get('choice')}", d.get("note")),
                        "pointer": f"logs/strategy/decisions.jsonl#{i}"})
    names = {r["participant"] for r in out if r.get("participant")}
    for r in out:
        low = r["text"].lower()
        r["participants"] = sorted({n for n in names if n.lower() in low} | ({r["participant"]} if r.get("participant") else set()))
        r["skus"] = sorted(set(SKU.findall(r["text"])))
    return out


def _profile(d, pointer, superseded):
    sides = {s: d.get(f"as_{s}") or {} for s in ("shop", "buyer")}
    return {"kind": "profile", "ts": d.get("updated"), "participant": d.get("name"), "superseded": superseded,
            "changed": d.get("changed"), "slice": max((v.get("slice") or "" for v in sides.values()), default="") or None,
            "text": _text(d.get("name"), *[f"as {s}: {v.get('summary', '')} {_claims(v.get('interpreted'))}"
                                           for s, v in sides.items() if v]),
            "pointer": pointer}


def content_words(query):
    return [w for w in WORD.findall((query or "").lower()) if w not in STOP]


def coverage(words, doc_tokens):
    """Share of the query's content words found in a record (a Korean word counts when one of its pieces is)."""
    if not words:
        return 0.0
    hit = 0
    for w in words:
        pieces = [w] + ([w[i:i + 2] for i in range(len(w) - 1)] if HANGUL.search(w) and len(w) > 2 else [])
        hit += any(p in doc_tokens for p in pieces)
    return hit / len(words)


def bm25(docs, query):
    q = [w for w in tokens(query) if w not in STOP]
    toks = [tokens(d["text"]) for d in docs]
    n = len(docs)
    avg = sum(map(len, toks)) / n if n else 0
    df = {}
    for t in toks:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    scores = []
    for t in toks:
        tf, s = {}, 0.0
        for w in t:
            tf[w] = tf.get(w, 0) + 1
        for w in set(q):
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf[w] * (K1 + 1) / (tf[w] + K1 * (1 - B + B * len(t) / (avg or 1)))
        scores.append(s)
    return scores


@tool("memory.search", "Find what our analysts, the strategy agent and the user wrote before: slice summaries, "
      "participant profiles, briefs, decisions. Filter by time (since/until), participant, kind or strategy version "
      "first, then ranked by words. Returns the best records with a pointer to each; 'no record' when nothing "
      "matches (then do not assume). For counts use eval.*; to see the raw lines behind a record use memory.raw.",
      {"type": "object", "required": ["query"],
       "properties": {"query": {"type": "string", "minLength": 1},
                      "kind": {"type": "string", "enum": ["market summary", "self summary", "profile", "brief", "decision"]},
                      "participant": {"type": "string"}, "since": {"type": "number"}, "until": {"type": "number"},
                      "min_version": {"type": "integer"}, "history": {"type": "boolean"},
                      "limit": {"type": "integer", "minimum": 1, "maximum": 10}}})
def search(ctx, query, kind=None, participant=None, since=None, until=None, min_version=None, history=False, limit=5):
    docs = [r for r in records()
            if (kind is None or r["kind"] == kind)
            and (participant is None or participant in r["participants"])
            and (since is None or (r.get("ts") or 0) >= since) and (until is None or (r.get("ts") or 0) < until)
            and (min_version is None or (r.get("version") or 0) >= min_version)
            and (history or not r.get("superseded"))]
    if not docs:
        return {"found": 0, "note": "no record within these filters"}
    scores, words = bm25(docs, query), content_words(query)
    ranked = sorted((x for x in zip(scores, docs) if x[0] > 0 and coverage(words, set(tokens(x[1]["text"]))) >= MIN_COVER),
                    key=lambda x: (-x[0], -(x[1].get("ts") or 0)))
    if not ranked:
        return {"found": 0, "searched": len(docs), "note": "no record matches these words; do not assume one exists"}
    return {"found": len(ranked), "searched": len(docs), "records": [
        {"kind": d["kind"], "ts": d.get("ts"), "slice": d.get("slice"), "participants": d["participants"][:8],
         "skus": d["skus"][:8], "superseded": d.get("superseded") or None, "score": round(s, 2),
         "text": d["text"][:400], "pointer": d["pointer"]} for s, d in ranked[:limit]]}


@tool("memory.raw", "The raw lines behind a record, a few at a time: a file under memory/ (raw events, ledger copy, "
      "index) or a pointer memory.search returned, optionally only lines containing a string.",
      {"type": "object", "required": ["pointer"],
       "properties": {"pointer": {"type": "string", "minLength": 1}, "contains": {"type": "string"},
                      "limit": {"type": "integer", "minimum": 1, "maximum": 50}}})
def raw(ctx, pointer, contains=None, limit=10):
    rel, _, line_no = pointer.partition("#")
    if ctx.caller == "audit" and rel.startswith(("raw/tools", "raw/models", "index/tool.", "index/model.")):
        raise Refused("the audit does not read other agents' tool calls", "check the brief against the market's data")
    base = paths.root() if rel.startswith("logs/") else paths.memory()
    f = (base / rel).resolve()
    if not str(f).startswith(str(base.resolve())) or not f.is_file():
        raise Refused(f"{pointer!r} is not a file under memory/ or logs/", "use a pointer from memory.search")
    lines = f.read_text(encoding="utf-8").splitlines()
    if line_no.isdigit():
        lines = lines[int(line_no):int(line_no) + 1]
    if contains:
        lines = [x for x in lines if contains in x]
    return {"pointer": pointer, "lines": [x[:600] for x in lines[-limit:]], "total": len(lines)}
