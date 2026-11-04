"""Audit a written summary against its sources: citations by code, meaning by two models of different vendors.

    py -m redesign audit MARKET.md

- citations: every [A12], [A12-15] must point at lines that exist in agent.md; [B] needs the /board notes.
- models: designer and reviewer read agent.md (with line numbers), the /board notes and the document, each on its
  own, and list findings: wrong, unsupported, bad_citation, missing, inference_wrong, unclear.
- a finding both models raise about the same place is marked "both".
Output: work/audit/<doc>.json and the printed summary.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from redesign import guard, llm, sources

TYPES = ("wrong", "unsupported", "bad_citation", "missing", "inference_wrong", "unclear")

SYSTEM = """You audit a Korean summary of a market's rules against the two official sources: the market's agent.md
(given with line numbers) and the score notes from its /board page. The summary cites [A<line>] for agent.md and
[B] for the board notes. Lines marked → are inferences the author drew; check that they follow.

Be exact and strict. Find:
- wrong: the summary says something the source contradicts or misreads (numbers, conditions, who/when).
- unsupported: a statement presented as a rule that the cited lines (or any source line) do not say.
- bad_citation: the content is right but the cited line numbers do not contain it; give the right lines.
- missing: a Round 3 rule in the sources (sections "Round 3" onward, board notes) that the summary leaves out
  and that changes how a shop or a buyer should act. Skip setup steps for joining and Round 2 notes.
- inference_wrong: a → inference that does not follow, or follows only with an unstated assumption.
- unclear: wording a reader would likely misread.
Do not report style. Answer with JSON only:
{"findings": [{"type": .., "where": "section number and a short quote of the summary, or the source line for missing",
"source_lines": "A123-125 or B", "detail": .., "fix": "the corrected Korean sentence, or empty"}]}"""


def citations(doc, n_lines):
    bad = []
    for m in re.finditer(r"\[([^\]]*A\d[^\]]*)\]", doc):
        for a, b in re.findall(r"A(\d+)(?:-(\d+))?", m.group(1)):
            lo, hi = int(a), int(b or a)
            if not (1 <= lo <= hi <= n_lines):
                bad.append(m.group(0))
    return bad


def ask(model, doc, numbered, board):
    prompt = (f"agent.md (line numbers first):\n<<<\n{numbered}\n>>>\n\n/board notes:\n<<<\n{board}\n>>>\n\n"
              f"Summary to audit:\n<<<\n{doc}\n>>>")
    for _ in range(2):
        try:
            ans = llm.ask(prompt, system=SYSTEM, model=model, tag=f"audit {model}")
            out = json.loads(re.search(r"\{.*\}", ans, re.S).group(0))["findings"]
            for f in out:
                f["by"] = model
            return out
        except (AttributeError, ValueError, KeyError, llm.LLMError) as e:
            err = str(e)
    return [{"type": "error", "by": model, "detail": err}]


def run(name):
    doc = (guard.OWN / name).read_text(encoding="utf-8")
    agent = sources.agent_md().splitlines()
    numbered = "\n".join(f"{i + 1}: {l}" for i, l in enumerate(agent))
    board = sources.board_notes()
    with ThreadPoolExecutor(2) as ex:
        a, b = ex.map(lambda m: ask(m, doc, numbered, board), ["designer", "reviewer"])
    out = {"doc": name, "bad_citations": citations(doc, len(agent)), "findings": a + b,
           "models": {m: llm.config()["roles"][m] for m in ("designer", "reviewer")}}
    guard.write(guard.WORK / "audit" / f"{name}.json", json.dumps(out, ensure_ascii=False, indent=1))
    return out
