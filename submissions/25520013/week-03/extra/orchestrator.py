"""The deterministic layer: records, scores, runs the work, checks it.

Nothing here is a model. This is the only component in the extended system
that cannot be persuaded, which is why it owns the evidence: it does not
receive outcomes from anyone, it produces them by running `verify`.

It is also outside the agent pool. The manager rotates task by task, so
today's manager is tomorrow's contractor; if the record or the check lived
with the manager, an agent would eventually be grading its own past.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import backend                                   # noqa: E402
from contract_net import contractor              # noqa: E402
import verify                                    # noqa: E402

K = 3           # prior strength: the record holds half the weight at n = K
SKILLS = ("calc", "write", "code")

WORK_SYSTEM = """You are contractor {name} in a contract net.
Your skill: {skill}.

You have been awarded the work below. Do it and reply with the answer only —
no preamble, no explanation of what you are about to do. If the work asks for
a number, reply with the number. If it asks for Python, reply with the code.
If it asks for a sentence, reply with that sentence."""


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

    def score(self, name, skill, confidence):
        """Shrink the claim toward the record as the record fills.

        n = 0 gives back the raw confidence, which is exactly the stage-1 rule,
        so the opening of every run is a stage-1 replication and no cold-start
        special case is needed.
        """
        n, wins, _ = self._cell(name, skill)
        w = n / (n + K)
        evidence = wins / n if n else 0.0
        return w * evidence + (1 - w) * (float(confidence) / 100.0)

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
        lines = ["RECORD — verified outcomes, computed by the orchestrator",
                 "         contractors cannot write or edit this",
                 "  name   " + "  ".join(f"{s:>7s}" for s in SKILLS)
                 + "   claimed -> actual   gap"]
        for name in names:
            cells = "  ".join(
                f"{self._cell(name, s)[1]:>3d}/{self._cell(name, s)[0]:<3d}"
                for s in SKILLS)
            n = sum(self._cell(name, s)[0] for s in SKILLS)
            if n:
                wins = sum(self._cell(name, s)[1] for s in SKILLS)
                claims = sum(self._cell(name, s)[2] for s in SKILLS)
                tail = (f"   {claims / n:5.1f} -> {100 * wins / n:5.1f}%"
                        f"   {100 * self.gap(name):+5.1f}")
            else:
                tail = "   no record yet"
            lines.append(f"  {name}      {cells}{tail}")
        return "\n".join(lines)


def do_work(member, text, meter, log):
    """The awarded contractor actually does the work. One model call."""
    system = WORK_SYSTEM.format(name=member["name"], skill=member["skill"])
    answer = backend.ask(system, text, meter)
    log(f"  [work] {member['name']} -> {answer.strip()[:160].replace(chr(10), ' | ')}")
    return answer


def judge(element, answer, log):
    """Run the pre-committed check. No model, no discretion."""
    passed = verify.check(element["verify"], answer)
    log(f"  [check] need={element['need']} {element['verify']['type']} "
        f"-> {'PASS' if passed else 'FAIL'}")
    return passed


def build_team(overconfident_name="A"):
    """Same three contractors as stage 1, same skills, one of them inflating."""
    skills = {
        "A": "arithmetic — you evaluate numeric expressions and return the number",
        "B": "plain-English writing — you rewrite and summarise text for people",
        "C": "Python — you write small, correct functions",
    }
    return [contractor(n, skills[n], overconfident=(n == overconfident_name))
            for n in "ABC"]
