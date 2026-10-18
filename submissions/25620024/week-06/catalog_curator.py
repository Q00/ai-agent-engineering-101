"""Week 06 challenge 1 — catalog_curator.py: part 4, the curator (pick one of the five, or ask).

    python catalog_curator.py "something wooden to keep about 40 books, not taller than my desk"

The curator is a separate harness call from the negotiator: it sees the buyer's words and the
five one-line summaries (no prices, no reserves), and returns either a pick or one question.
Its context is thrown away after the pick; the negotiator later sees only the chosen item.

Code guards around it:
  - a pick that is not one of the five candidates is not accepted (no invented ids)
  - no candidates, or a vague request: the answer must be a question
  - a harness failure becomes a generic question, never a guess
"""
import json
import sys

from harness import ask
from catalog_search import load_catalog, parse_request, search, is_vague, summary

CURATOR_PROMPT = """You are a shop assistant helping a buyer choose furniture.
The buyer said: "{request}"
Candidate items from the catalog (the only items you may choose from):
{candidates}
{vague_note}
Choose the one item that best fits what the buyer said. If two fit equally, choose one and say why.
If none of them fits, or the buyer has not said enough to choose, ask the buyer ONE short question instead.
Reply with one JSON object only, either
{{"pick": "<item id from the list>", "reason": "<one short sentence for the buyer>"}}
or
{{"question": "<one short question for the buyer>"}}"""

GENERIC_QUESTION = "Could you tell me a bit more about what you need (size, material, or how you will use it)?"


def curate(request, candidates, vague):
    """Returns {"pick": item, "reason": str} or {"question": str}, plus "note" on guard hits."""
    if not candidates:
        return {"question": GENERIC_QUESTION, "note": "no candidates"}
    ids = {it["id"]: it for it in candidates}
    vague_note = ("Note: the request is vague and many items match; prefer asking one question."
                  if vague else "")
    prompt = CURATOR_PROMPT.format(request=request, vague_note=vague_note,
                                   candidates="\n".join(summary(it) for it in candidates))
    try:
        answer = ask(prompt)
    except Exception as e:
        return {"question": GENERIC_QUESTION, "note": f"harness error: {e}"}
    if answer.get("question"):
        return {"question": str(answer["question"])}
    pick = answer.get("pick")
    if pick not in ids:
        return {"question": GENERIC_QUESTION, "note": f"GUARD pick {pick!r} is not one of the candidates"}
    if vague:
        return {"question": GENERIC_QUESTION, "note": f"GUARD request is vague; not selling {pick} on a guess"}
    return {"pick": ids[pick], "reason": str(answer.get("reason", ""))}


if __name__ == "__main__":
    request = " ".join(sys.argv[1:]) or "something wooden to keep about 40 books, not taller than my desk"
    print("request  :", request)
    q = parse_request(request)
    top, n, relaxed = search(load_catalog(), q)
    vague = is_vague(q, n)
    print(f"search   : {n} matches" + (f", relaxed {relaxed}" if relaxed else "") + (", VAGUE" if vague else ""))
    for it in top:
        print("   ", summary(it))
    result = curate(request, top, vague)
    if "pick" in result:
        it = result["pick"]
        print(f"curator  : PICK {it['id']} {it['name']} -- {result['reason']}")
        print(f"           (code now locks this item; its reserve {it['reserve']} goes to the negotiation guard)")
    else:
        print(f"curator  : QUESTION {result['question']}")
    if result.get("note"):
        print("note     :", result["note"])
