"""The shop's own notebook, kept the way the owner's personal agent keeps its memory (OpenClaw):

    memory/agent/MEMORY.md                 long-term notes, capped at memory.long_term_chars; durable facts and decisions
    memory/agent/daily/YYYY-MM-DD.md       what happened today, one line per event, raw
    memory/agent/strategies/<slug>.md      a lesson from a mistake: "triggers: a, b" then what to do differently
    memory/agent/daily/archive/            daily notes already folded into MEMORY.md (moved, never deleted)

A decision gets only what fits it (select): the long-term notes, the lessons whose triggers match what the decision is
about, and recent lines that mention it, within memory.select_chars. maybe_consolidate folds old days into one line each
of MEMORY.md and keeps it under its cap, oldest first. Terra keeps this tidy; Astra may change how it works.
Nothing here can stop a restock: every function swallows its own errors.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"long_term_chars": 4000, "daily_keep_days": 3, "select_chars": 2500, "consolidate_hours": 6}
SEED = """# Shop memory (nocha12)

## Standing
- Round 3 profit = balance - 5,000,000 - bankruptcy aid. Unsold stock is a loss; selling below cost is a loss.
- Start cautious; widen limits only where my own sales show it pays (pricing.json, moved by the meta-harness).

## Learned

## Past days
"""


def root():
    """The live notebook when the shop runs under its market name (run_market.sh sets it), a scratch one otherwise (tests)."""
    if os.environ.get("AGENT_MEMORY_DIR"):
        return os.environ["AGENT_MEMORY_DIR"]
    if os.environ.get("SHOP_MARKET_NAME"):
        return os.path.join(HERE, "memory", "agent")
    return os.path.join(tempfile.gettempdir(), "nocha12-agent-memory-test")


def cfg():
    try:
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            return {**DEFAULTS, **(json.load(f).get("memory") or {})}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def read(path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def long_term():
    path = os.path.join(root(), "MEMORY.md")
    text = read(path)
    if not text:
        write(path, SEED)
        text = SEED
    return text


def daily(md, kind, text, now=None):
    """One line in today's note."""
    try:
        now = now or time.time()
        path = os.path.join(root(), "daily", time.strftime("%Y-%m-%d", time.localtime(now)) + ".md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = " ".join(str(text).split())[:500]
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- {time.strftime('%H:%M', time.localtime(now))} [{kind}] {line}\n")
    except OSError as e:
        print(f"[memory] daily note failed: {e}", file=sys.stderr)


def slug(text):
    return re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", text).strip("-")[:60] or "note"


def strategy(name, triggers, text):
    """Writes (or rewrites) one lesson. Triggers are the words that make it relevant: SKUs, categories, food/goods."""
    write(os.path.join(root(), "strategies", slug(name) + ".md"),
          "triggers: " + ", ".join(t for t in triggers if t) + "\n" + text.strip() + "\n")


def strategies():
    """[(name, [triggers], text)]."""
    folder, out = os.path.join(root(), "strategies"), []
    for n in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if n.endswith(".md"):
            body = read(os.path.join(folder, n))
            first, _, rest = body.partition("\n")
            trig = [t.strip() for t in first.removeprefix("triggers:").split(",") if t.strip()] if first.startswith("triggers:") else []
            out.append((n[:-3], trig, rest.strip() if trig else body.strip()))
    return out


def learn_from_outcomes(md, results, catalog):
    """A SKU that was thrown away unsold, or has sat unsold a day, becomes a lesson tied to that SKU and its category."""
    try:
        for r in (results or {}).get("never_sold", []) + [r for r in (results or {}).get("best_skus", []) if r.get("gone_unsold")]:
            x = catalog.get(r["sku"]) or {}
            if not r.get("gone_unsold") and r.get("hours_since_first", 0) < 24:
                continue
            what = (f"{r['gone_unsold']} of {r['asked']} thrown away unsold" if r.get("gone_unsold")
                    else f"{r['left']} of {r['asked']} still unsold after {r['hours_since_first']:.0f} h")
            strategy(f"unsold-{r['sku']}", [r["sku"], x.get("category"), x.get("kind")],
                     f"{x.get('name') or r['sku']} ({x.get('kind')}, {x.get('category')}): {what}, sold {r['sold']}. "
                     "Buy it again only in small lots and only after the ledger shows recent sales; for food mind the shelf life.")
    except (OSError, KeyError, TypeError) as e:
        print(f"[memory] learning failed: {type(e).__name__}: {e}", file=sys.stderr)


def lessons_for(keys, limit=600):
    """Only the lessons whose triggers match, short: what a negotiation turn can afford to read."""
    try:
        text = "\n".join(f"- {t}" for n, trig, t in strategies() if any(k in trig for k in keys if k))
        return text[:limit]
    except OSError:
        return ""


def recent_lines(days=2, now=None):
    now, out = now or time.time(), []
    for back in range(days - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(now - back * 86400))
        out += read(os.path.join(root(), "daily", day + ".md")).splitlines()
    return out


def select(md, keys, limit=None):
    """The memory text for one decision: long-term notes, matching lessons, recent matching lines, newest last."""
    try:
        limit = limit or cfg()["select_chars"]
        keys = [k for k in keys if k]
        hit = lambda text: any(k in text or (">" in k and k.split(">")[-1] in text) for k in keys)
        lessons = [f"- {n}: {t}" for n, trig, t in strategies() if any(k in trig or any(k.startswith(x) for x in trig if ">" in x) for k in keys) or hit(t)]
        lines = recent_lines()
        near = [ln for ln in lines if hit(ln)][-15:] or lines[-5:]
        parts = [long_term()[: limit // 2], "Lessons for this decision:\n" + ("\n".join(lessons[:8]) or "(none yet)"),
                 "Recent:\n" + ("\n".join(near) or "(nothing yet)")]
        text = "\n\n".join(parts)
        return text if len(text) <= limit else text[: limit - 20] + "\n...(cut)"
    except Exception as e:   # noqa: BLE001 - a decision goes on without memory rather than fail
        return f"(memory unavailable: {type(e).__name__})"


def cap(text, limit):
    """Keeps MEMORY.md under limit by dropping the oldest 'Past days' lines, then the oldest 'Learned' lines."""
    lines = text.splitlines()
    for section in ("## Past days", "## Learned"):
        while len("\n".join(lines)) > limit:
            try:
                start = lines.index(section)
            except ValueError:
                break
            first = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("- ")), None)
            if first is None or any(lines[j].startswith("## ") for j in range(start + 1, first)):
                break
            del lines[first]
    return "\n".join(lines)[:limit] + "\n"


def add_line(section, line):
    """Adds one bullet under a section of MEMORY.md (Learned or Past days) and keeps the cap."""
    text = long_term()
    lines = text.splitlines()
    if section not in lines:
        lines += ["", section]
    i = lines.index(section) + 1
    while i < len(lines) and not lines[i].startswith("## "):
        i += 1
    while i > 0 and not lines[i - 1].strip():
        i -= 1
    lines.insert(i, "- " + " ".join(line.split()))
    write(os.path.join(root(), "MEMORY.md"), cap("\n".join(lines), cfg()["long_term_chars"]))


def maybe_consolidate(md, now=None):
    """Every consolidate_hours: days older than daily_keep_days become one line each under 'Past days'."""
    try:
        c, now = cfg(), now or time.time()
        state_path = os.path.join(md, "memory_state.json")
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {"at": 0}
        if now - state["at"] < c["consolidate_hours"] * 3600:
            return False
        folder = os.path.join(root(), "daily")
        keep_from = time.strftime("%Y-%m-%d", time.localtime(now - c["daily_keep_days"] * 86400))
        for n in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            if not n.endswith(".md") or n[:-3] >= keep_from:
                continue
            lines = read(os.path.join(folder, n)).splitlines()
            kinds = {}
            for ln in lines:
                m = re.match(r"- \d\d:\d\d \[([^\]]+)\]", ln)
                if m:
                    kinds[m.group(1)] = kinds.get(m.group(1), 0) + 1
            add_line("## Past days", f"{n[:-3]}: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) +
                     (f"; last: {lines[-1][8:160]}" if lines else ""))
            os.makedirs(os.path.join(folder, "archive"), exist_ok=True)
            shutil.move(os.path.join(folder, n), os.path.join(folder, "archive", n))
        write(os.path.join(root(), "MEMORY.md"), cap(long_term(), c["long_term_chars"]))
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"at": now}, f)
        return True
    except Exception as e:   # noqa: BLE001
        print(f"[memory] consolidate failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def search(query, limit=20):
    """Lines of the whole notebook that contain query (the my_memory tool)."""
    out, q = [], query.lower()
    files = [("MEMORY.md", os.path.join(root(), "MEMORY.md"))]
    for sub in ("strategies", "daily", os.path.join("daily", "archive")):
        folder = os.path.join(root(), sub)
        files += [(os.path.join(sub, n), os.path.join(folder, n)) for n in sorted(os.listdir(folder), reverse=True)
                  if n.endswith(".md")] if os.path.isdir(folder) else []
    for name, path in files:
        for ln in read(path).splitlines():
            if q in ln.lower():
                out.append({"file": name, "line": ln[:300]})
                if len(out) >= limit:
                    return out
    return out
