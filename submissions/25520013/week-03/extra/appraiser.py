"""The appraiser: a disinterested third party that says whose job this is.

The bid is the one number stage 1 showed cannot be trusted — a contractor
states it about itself, before the work, with a contract riding on it. Smith's
1980 bids were honest because they were measurements of hardware; the route
taken here is the other one, removing the interest rather than the judgement.

The appraiser never bids and never works, so no answer it gives can win it
anything. It sees the fragment and the three persona lines, and nothing else:
not the gold label, not the verification rules, not the record, not the bids.
Its output is a distribution over the contractors, which is deliberate — a flat
0.33/0.33/0.33 is how it says the three are interchangeable, and a team of one
model in three costumes should be able to come out looking exactly like that.

It replaces the confidence term in the award score and nothing else, so the arm
that uses it differs from the arm that does not by one factor. Confidence is
still collected and still logged; it just stops deciding.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import backend                                   # noqa: E402

_JSON_RX = re.compile(r"\{.*\}", re.S)

APPRAISE_SYSTEM = """You appraise contract net fragments. You are not a
contractor: you never bid and you never do the work, so no answer wins or
costs you anything.

The contractors are:
{roster}

Given one fragment, say how likely each contractor is to be the right owner of
it. Answer with one JSON object and nothing else, with one number per
contractor summing to 1, and a short reason:

{{"A": 0.5, "B": 0.3, "C": 0.2, "reason": "..."}}

If the three look interchangeable for this fragment, say so with equal numbers.
Do not flatter any contractor and do not assume its self-description is true."""


def appraise(fragment, team, meter, log):
    """Return (prior per contractor, whether the reply had to be defaulted)."""
    names = [member["name"] for member in team]
    roster = "\n".join(f"  {m['name']}: {m['skill']}" for m in team)
    reply = backend.ask(APPRAISE_SYSTEM.format(roster=roster),
                        f"FRAGMENT: {fragment}", meter)
    flat = {name: 1.0 / len(names) for name in names}

    match = _JSON_RX.search(reply or "")
    if not match:
        log(f"  [fit ] unparseable — falling back to flat {flat[names[0]]:.2f}")
        return flat, True
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        log(f"  [fit ] unparseable — falling back to flat {flat[names[0]]:.2f}")
        return flat, True

    try:
        raw = {name: max(0.0, float(payload.get(name, 0.0))) for name in names}
    except (TypeError, ValueError):
        log("  [fit ] non-numeric — falling back to flat")
        return flat, True
    total = sum(raw.values())
    if total <= 0:
        log("  [fit ] all zero — falling back to flat")
        return flat, True

    prior = {name: value / total for name, value in raw.items()}
    log("  [fit ] " + "  ".join(f"{n}={prior[n]:.2f}" for n in names)
        + f" :: {str(payload.get('reason', ''))[:80]}")
    return prior, False
