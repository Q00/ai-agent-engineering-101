"""The deterministic layer: records, scores, runs the work, checks it.

Nothing here is a model. This is the only component in the extended system
that cannot be persuaded, which is why it owns the evidence: it does not
receive outcomes from anyone, it produces them by running `verify`.

It is also outside the agent pool. The manager rotates task by task, so
today's manager is tomorrow's contractor; if the record or the check lived
with the manager, an agent would eventually be grading its own past.
"""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import backend  # noqa: E402
from contract_net import contractor  # noqa: E402

import tools  # noqa: E402
import verify  # noqa: E402

K = 3  # prior strength: the record holds half the weight at n = K
MAX_TOOL_CALLS = 3
SKILLS = ("calc", "write", "code")

WORK_SYSTEM = """You are contractor {name} in a contract net.
Your skill: {skill}.

{tools}

You have been awarded the work below. Every reply you send is exactly one of
these two, and nothing else:

  1. a tool call — the JSON object on its own, no words around it
  2. your finished answer — the answer on its own, no preamble and no
     explanation of what you are about to do

Use a tool first whenever one can check your answer; a checked answer beats a
confident one. When you answer: if the work asks for a number, send the
number; if it asks for Python, send the code; if it asks for a sentence, send
that sentence."""

_TOOLED = """Tools you may call:
{lines}

Budget: {budget} tool calls for this work item."""

_JSON_FENCE_RX = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_OBJECT_RX = re.compile(r"\{(?:[^{}]|\{[^{}]*\})*\}", re.S)


class Record:
    """Verified outcomes per (contractor, skill). The only thing that accrues.

    Three numbers per cell: attempts, passes, and the sum of the confidences
    the contractor claimed on those attempts. The third is never used in the
    score — it is kept so the calibration gap can be reported as evidence.
    """

    def __init__(self):
        self.cells = {}

    def _cell(self, name, skill):
        return self.cells.setdefault((name, skill), [0, 0, 0.0])

    def note(self, name, skill, passed, confidence):
        cell = self._cell(name, skill)
        cell[0] += 1
        cell[1] += int(bool(passed))
        cell[2] += float(confidence or 0.0)

    def score(self, name, skill, prior):
        """Shrink the prior toward the record as the record fills.

        `prior` is what is believed before any work: the contractor's own
        confidence in one arm, the appraiser's estimate in another, both on
        0..1. At n = 0 the score is the prior untouched, so an arm carrying
        confidence opens as an exact stage-1 replication and needs no
        cold-start special case.
        """
        n, wins, _ = self._cell(name, skill)
        w = n / (n + K)
        evidence = wins / n if n else 0.0
        return w * evidence + (1 - w) * float(prior)

    def gap(self, name):
        """Mean claimed confidence minus actual pass rate, over all skills."""
        n = sum(self._cell(name, s)[0] for s in SKILLS)
        if not n:
            return None
        wins = sum(self._cell(name, s)[1] for s in SKILLS)
        claims = sum(self._cell(name, s)[2] for s in SKILLS)
        return (claims / n / 100.0) - (wins / n)

    def table(self, names):
        """The block handed to an LLM manager. Contractors cannot write this."""
        lines = [
            "RECORD — verified outcomes, computed by the orchestrator",
            "         contractors cannot write or edit this",
            "  name   "
            + "  ".join(f"{s:>7s}" for s in SKILLS)
            + "   claimed -> actual   gap",
        ]
        for name in names:
            cells = "  ".join(
                f"{self._cell(name, s)[1]:>3d}/{self._cell(name, s)[0]:<3d}"
                for s in SKILLS
            )
            n = sum(self._cell(name, s)[0] for s in SKILLS)
            if n:
                wins = sum(self._cell(name, s)[1] for s in SKILLS)
                claims = sum(self._cell(name, s)[2] for s in SKILLS)
                tail = (
                    f"   {claims / n:5.1f} -> {100 * wins / n:5.1f}%"
                    f"   {100 * self.gap(name):+5.1f}"
                )
            else:
                tail = "   no record yet"
            lines.append(f"  {name}      {cells}{tail}")
        return "\n".join(lines)


def _tool_block(name):
    """The tool section of a contractor's system prompt, or a bare line."""
    owned = tools.owned_by(name)
    if not owned:
        return "You have no tools. Answer from your own reasoning."
    return _TOOLED.format(
        lines="\n".join(f"  {tools.DESCRIPTION[t]}" for t in owned),
        budget=MAX_TOOL_CALLS,
    )


def _request(answer):
    """Read a tool call out of a reply, or None if the reply is an answer.

    The whole reply is tried first, because a tool call is supposed to be the
    whole reply and `json.loads` then handles braces inside the arguments —
    which matters for `run_tests`, whose argument is code. The scan for an
    embedded object is only the fallback for a reply with chatter around it.
    """
    text = (answer or "").strip()
    fence = _JSON_FENCE_RX.search(text)
    for candidate in (
        ([fence.group(1)] if fence else [])
        + [text]
        + [m.group(0) for m in _OBJECT_RX.finditer(text)]
    ):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("tool"), str):
            args = payload.get("args")
            return payload["tool"], args if isinstance(args, dict) else {}
    return None


def _followup(text, tool, output, left):
    budget = (
        f"You may call a tool {left} more time(s), or reply with the final answer only."
        if left
        else "Your tool budget is spent. Reply with the final answer only."
    )
    return f"{text}\n\nYou called {tool} and it returned:\n{output}\n\n{budget}"


def do_work(member, text, meter, log, specs=()):
    """The awarded contractor does the work, with only its own tools.

    The permission table is enforced here rather than in the prompt, because a
    prompt is a request and this has to be a wall: a contractor that reaches
    outside its specialty gets a refusal and spends a turn, and the reach is
    logged. That is the point of the asymmetry — the same reply that would be
    a cheap guess bare-handed is a checked answer for the one contractor whose
    tool can check it, which is what made Smith's sensor-holding node the
    right bidder rather than merely the loudest one.

    `specs` are the committed checks for the elements this fragment serves.
    Only `run_tests` receives them, and only as a verdict.
    """
    name = member["name"]
    system = WORK_SYSTEM.format(
        name=name, skill=member["skill"], tools=_tool_block(name)
    )
    turn, answer, used, refused = text, "", 0, 0
    for used in range(MAX_TOOL_CALLS + 1):
        left = MAX_TOOL_CALLS - used
        answer = backend.ask(system, turn, meter)
        request = _request(answer)
        if request is None or left == 0:
            break
        tool, args = request
        output, denied = tools.call(tool, args, name, specs)
        refused += int(denied)
        log(f"  [tool] {name} {tool} -> {output.strip()[:120].replace(chr(10), ' | ')}")
        turn = _followup(text, tool, output, left - 1)
    log(
        f"  [work] {name} ({used} tool call(s), {refused} refused) -> "
        f"{answer.strip()[:160].replace(chr(10), ' | ')}"
    )
    return answer, used, refused


def judge(element, answer, log):
    """Run the pre-committed check. No model, no discretion."""
    passed = verify.check(element["verify"], answer)
    log(
        f"  [check] need={element['need']} {element['verify']['type']} "
        f"-> {'PASS' if passed else 'FAIL'}"
    )
    return passed


def build_team(overconfident_name="A"):
    """Same three contractors as stage 1, same skills, one of them inflating."""
    skills = {
        "A": "arithmetic — you evaluate numeric expressions and return the number",
        "B": "plain-English writing — you rewrite and summarise text for people",
        "C": "Python — you write small, correct functions",
    }
    return [
        contractor(n, skills[n], overconfident=(n == overconfident_name)) for n in "ABC"
    ]
