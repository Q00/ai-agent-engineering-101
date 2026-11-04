"""Where rules come from, cut into chunks a model can read.

A chunk is {id, source, loc, text}: id is a hash of the text, so an unchanged chunk is never extracted twice.
Sources: the market's agent.md (fetched, cached in work/sources/), the harness's design documents and notes
(read as data), and the week-08 commit messages.
"""
import hashlib
import json
import re
import time
import urllib.request

from redesign import guard

AGENT_MD = "https://market.ourolabs.ai/agent.md"
DOCS = ["HARNESS.md", "design/harness-v5.md", "design/buyer-parallel.md", "card/system_card.md",
        "logs/strategy/user_notes.md", "logs/strategy/overnight-report-20261104.md", "logs/strategy/URGENT.md"]
JSONL = ["logs/strategy/decisions.jsonl", "logs/strategy/changes.jsonl"]
SIZE = 6000                                        # characters per chunk, about 1.5k tokens


def _id(source, text):
    return hashlib.sha1((source + "\n" + text).encode("utf-8")).hexdigest()[:12]


def agent_md(refresh=False):
    p = guard.WORK / "sources" / "agent.md"
    if refresh or not p.exists():
        with urllib.request.urlopen(AGENT_MD, timeout=30) as r:
            text = r.read().decode("utf-8")
        guard.write(p, text)
        guard.write(p.with_suffix(".fetched"), time.strftime("%Y-%m-%dT%H:%M:%S"))
    return p.read_text(encoding="utf-8")


def markdown_chunks(source, text):
    """Cut at headings; join small sections and split big ones so each chunk is near SIZE."""
    lines = text.splitlines()
    sections, start, head = [], 0, ""
    for i, line in enumerate(lines + ["# end"]):
        if re.match(r"#{1,4} ", line) or i == len(lines):
            if i > start:
                sections.append((head, start + 1, i, "\n".join(lines[start:i])))
            start, head = i, line.lstrip("# ").strip()
    out, buf = [], []
    def flush():
        if buf:
            heads = [b[0] for b in buf if b[0]]
            loc = f"{source} lines {buf[0][1]}-{buf[-1][2]}" + (f" ({heads[0]})" if heads else "")
            body = "\n".join(b[3] for b in buf)
            out.append({"id": _id(source, body), "source": source, "loc": loc, "text": body})
            buf.clear()
    for s in sections:
        if len(s[3]) > SIZE:
            flush()
            paras, part, first = s[3].split("\n\n"), "", s[1]
            for p in paras:
                if part and len(part) + len(p) > SIZE:
                    buf.append((s[0], first, first + part.count("\n"), part))
                    flush()
                    first += part.count("\n") + 2
                    part = ""
                part = part + "\n\n" + p if part else p
            if part:
                buf.append((s[0], first, s[2], part))
                flush()
            continue
        if buf and sum(len(b[3]) for b in buf) + len(s[3]) > SIZE:
            flush()
        buf.append(s)
    flush()
    return out


def _packed(source, items):
    """items: [(loc, text)] packed into chunks near SIZE."""
    out, buf = [], []
    def flush():
        if buf:
            body = "\n\n".join(t for _, t in buf)
            loc = f"{source} {buf[0][0]}" + (f" .. {buf[-1][0]}" if len(buf) > 1 else "")
            out.append({"id": _id(source, body), "source": source, "loc": loc, "text": body})
            buf.clear()
    for loc, text in items:
        if buf and sum(len(t) for _, t in buf) + len(text) > SIZE:
            flush()
        buf.append((loc, text))
    flush()
    return out


def all_chunks():
    chunks = markdown_chunks("agent.md", agent_md())
    for d in DOCS:
        chunks += markdown_chunks(d, guard.read(d))
    for j in JSONL:
        rows = [l for l in guard.read(j).splitlines() if l.strip()]
        chunks += _packed(j, [(f"line {i + 1}", l) for i, l in enumerate(rows)])
    commits = [(h, f"commit {h} {date[:16]}\n{msg}") for h, date, msg in guard.git_log()]
    chunks += _packed("git log", commits)
    return chunks


def summary(chunks):
    by = {}
    for c in chunks:
        n, chars = by.get(c["source"], (0, 0))
        by[c["source"]] = (n + 1, chars + len(c["text"]))
    return by


if __name__ == "__main__":
    cs = all_chunks()
    for s, (n, chars) in summary(cs).items():
        print(f"{s}: {n} chunks, {chars} chars")
    print(json.dumps(cs[0], ensure_ascii=False)[:300])
