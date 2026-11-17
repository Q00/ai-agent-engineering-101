"""Procedural memory: what the agent has learned to do, with the evidence, and the alarms nobody was there to hear.

    .market/lessons.jsonl   one JSON object per line: at, source, scope, lesson, evidence, expect
    lessons_seed.jsonl      the lessons of Round 3 up to 11-17, taken from REPORT.md's incident log; they are the
                            starting content of .market/lessons.jsonl on a machine that has none

source is "incident" (a person or a coding session found it), "reflection" (the nightly model wrote it, with the
number it expects to move), "rulebook" (the market's rules page changed) or "alert" (something the agent could not
do, e.g. the model did not answer). tune.py shows the newest lessons to the nightly reflection and appends its own.

Why: for two weeks the lessons lived in commit messages and in REPORT.md, where the agent cannot read them. The
tuner moved seller_volume_share 0.4 -> 0.5 -> 0.6 -> 0.7 -> 0.6 -> 0.7 over six nights because each night it saw one
day and none of its earlier reasons; and its login expired on 11-15 21:48, two reflections and 1,150 bargaining
calls went unanswered, and the first notice was a person reading tune.log on 11-17.
"""
import json
import os
import time
from pathlib import Path

FILE = "lessons.jsonl"
SEED = Path(__file__).resolve().parent / "lessons_seed.jsonl"


def path(state_dir=None):
    return Path(state_dir or os.environ.get("MARKET_DIR", ".market")) / FILE


def read_jsonl(p):
    out = []
    try:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def load(state_dir=None):
    """The recorded lessons, oldest first; the seed when this machine has recorded none yet."""
    p = path(state_dir)
    return read_jsonl(p) if p.exists() else read_jsonl(SEED)


def add(source, lesson, state_dir=None, **fields):
    """Append one lesson. Returns the stored entry."""
    p = path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():   # start from the seed, so the file is the whole memory
        p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in read_jsonl(SEED)), encoding="utf-8")
    entry = {"at": time.strftime("%Y-%m-%d %H:%M"), "source": source, "lesson": " ".join(str(lesson).split())[:400],
             **{k: v for k, v in fields.items() if v not in (None, "", {}, [])}}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print("[strategy] " + json.dumps({"event": "lesson", **entry}, ensure_ascii=False, sort_keys=True), flush=True)
    return entry


def digest(lessons, limit=10, chars=230):
    """The newest lessons as short lines for the reflection's context."""
    lines = []
    for e in lessons[-limit:]:
        text = e.get("lesson", "")
        if e.get("evidence"):
            text += f" ({e['evidence']})"
        if e.get("expect"):
            text += f" Expected: {e['expect']}"
        lines.append(f"- {str(e.get('at', ''))[5:10]} [{e.get('source', '?')}/{e.get('scope', '-')}] {text[:chars]}")
    return lines
