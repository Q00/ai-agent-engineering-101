"""Round-3 starter interface using the existing bounded direct-API harness.
No coding CLI, tools, extra credentials, or higher token limits.
Seller pricing/accept/reject model: SELLER_MODEL exactly (slot-recovery-v1 authorization user1570233935940554783).
A model error returns no decision and seller_r3 takes its deterministic contractual fallback; never another model.
The buyer keeps openai_harness.MODEL (nano) through its own import.
"""
import json
from openai_harness import complete

SELLER_MODEL = 'gpt-6.1-terra'
CMD = 'OpenAI-API ' + SELLER_MODEL


def ask_json(prompt):
    raw = complete(prompt, model=SELLER_MODEL)
    try:
        decision = json.loads(raw)
    except (ValueError, TypeError):
        return None, raw
    return (decision if isinstance(decision, dict) else None), raw
