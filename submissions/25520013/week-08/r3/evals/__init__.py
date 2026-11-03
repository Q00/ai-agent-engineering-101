"""v1.18 evaluation and checks for the shadow memory layers (report 6). Offline: synthetic ledgers or a state dir.

    evals.synth        seeded synthetic ledger + question set (no memory/ import)
    evals.truth        brute-force answers from the raw events (no memory/ import)
    evals.answer       answers from the memory layers, plus the broken variants
    evals.memory_eval  LongMemEval-V2-style score table (python -m evals.memory_eval --synthetic --seed 7)
    evals.context_eff  memory payload vs raw ledger slice size
    evals.forum_check  forum posts vs the ledger window they summarise

ABSTAIN equals memory.ABSTAIN (evals.answer asserts it); the truth side keeps its own copy so it imports nothing
from memory/.
"""

ABSTAIN = "__abstain__"
ABILITIES = ("IE", "MS", "TR", "KU", "AB")
ABILITY_NAMES = {
    "IE": "information extraction",
    "MS": "multi-session reasoning",
    "TR": "temporal reasoning",
    "KU": "knowledge update",
    "AB": "abstention",
}
