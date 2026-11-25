"""The system card: one document every agent reads, whatever its model (HARNESS.md 1).

card/system_card.md is cut into sections on its "## " headings. assemble() puts together what one
reader gets, in a fixed order so the unchanging part comes first and prompt caching can hit (C1):

    core sections -> the reader's role -> intent (framed for that reader) -> the strategy in force
    (version and phase, the context the analysts and strategy agent wrote, the hypothesis under test,
    limit values, then priorities)

An executor gets only its own role. Other roles are for the analysts, strategy and audit
(memory.card_section enforces this).

The intent is the same text for everyone; only its frame differs (HARNESS.md 1): executors follow it,
the strategy agent develops it, the audit agent verifies it.
"""
import re
from pathlib import Path

from core import paths

FRAMES = {
    "executor": "The user's intent below is the direction to follow.",
    "strategist": "The user's intent below is your starting point; develop it from the records.",
    "auditor": "The user's intent and the current hypothesis below are claims to verify against the scoring "
               "formula and the data, not directions to follow.",
}


def card_path() -> Path:
    """The card next to the code (11/6: a trial run of new code reads live records through HARNESS_ROOT, and the 02:00
    meta cycle then read the live folder's older card); the records' copy only when the code has none."""
    own = Path(__file__).resolve().parent.parent / "card" / "system_card.md"
    return own if own.exists() else paths.root() / "card" / "system_card.md"


def parse(text):
    """(card_version, {section name: text}) from the card's front matter and '## ' headings."""
    m = re.search(r"^card_version:\s*(\d+)", text, re.M)
    version = int(m.group(1)) if m else None
    body = re.sub(r"<!--.*?-->", "", text.split("\n---", 2)[-1] if text.startswith("---") else text, flags=re.S)
    sections = {}
    for chunk in re.split(r"^## ", body, flags=re.M)[1:]:
        title, _, content = chunk.partition("\n")
        sections[title.strip()] = content.strip()
    return version, sections


def load():
    return parse(card_path().read_text(encoding="utf-8"))


def section(name):
    """One section by name, for memory.card_section."""
    _, s = load()
    if name not in s:
        raise KeyError(f"no section {name!r}; sections: {sorted(s)}")
    return s[name]


_LIMIT_NAMES = {"cash_floor": "cash floor (KRW)", "max_units_per_sku": "units per SKU",
                "max_stock_share": "stock value, share of the 5,000,000 start",
                "food_share_max": "food, share of the stock value"}


def _limit_lines(limits):
    lines = [f"- {_LIMIT_NAMES.get(k, k)}: {v}" for k, v in limits.items() if k != "below_cost"]
    b = limits.get("below_cost") or {}
    if b.get("allowed"):
        phase = f" in phase {b['only_in_phase']}" if b.get("only_in_phase") else ""
        lines.append(f"- below cost: allowed{phase}, losing at most {b.get('max_loss_share_of_cost')} of cost per deal")
    else:
        lines.append("- below cost: not allowed")
    return lines


def assemble(role: str, reader: str = "executor", priorities=None, limits=None, strategy=None):
    """(card_version, text) for one reader. role is a 'role: ...' section name without the prefix."""
    version, s = load()
    if f"role: {role}" not in s:
        raise KeyError(f"no role {role!r}; roles: {sorted(k[6:] for k in s if k.startswith('role: '))}")
    if reader not in FRAMES:
        raise KeyError(f"reader must be one of {sorted(FRAMES)}")
    parts = []
    try:                        # user 11/11: the market's own rules, read by harness.rules_digest, come first
        from harness import rules_digest
        top = rules_digest.text()
        if top:
            parts.append(top)
    except Exception:
        pass
    parts += [f"# {k}\n{v}" for k, v in s.items() if k.startswith("core: ")]
    parts.append(f"# your role: {role}\n{s['role: ' + role]}")
    parts.append(f"# intent\n{FRAMES[reader]}\n\n{s['intent']}")
    if strategy:
        priorities = priorities or strategy.get("priorities")
        limits = limits or strategy.get("limits")
        head = [f"# strategy in force (v{strategy.get('version')}, epoch {strategy.get('epoch')}, "
                f"phase {strategy.get('phase')})"]
        if strategy.get("context"):
            head.append("What the analysts and the strategy agent saw:")
            head += [f"- {c}" for c in strategy["context"]]
        hyp = (strategy.get("experiment") or {}).get("hypothesis")
        if hyp:
            head.append(f"Hypothesis under test: {hyp}")
        parts.append("\n".join(head))
    if limits:
        parts.append("# risk limits in force (a first guess the strategy revises; tools enforce them)\n"
                     + "\n".join(_limit_lines(limits)))
    if priorities:
        parts.append("# current priorities (strategy in force)\n" + "\n".join(f"- {p}" for p in priorities))
    return version, "\n\n".join(parts)
