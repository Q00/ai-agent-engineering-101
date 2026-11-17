"""One round trip with the exact host settings, before any experiment run:
tool call -> tool result -> follow-up call -> strict JSON final message.

    OPENROUTER_API_KEY=<key> python probe_luna.py > probes/luna-<date>.txt

The tools are stubs (no market server): only the API combination is under test,
tools + response_format(json_schema, strict) + reasoning effort low on
openai/gpt-6-luna through OpenRouter. If it fails, the failure is the result.
"""
import json
import sys

from llm import FINAL, LLM, MODEL, PROVIDER, REASONING


def emit(event, **f):
    print(json.dumps({"event": event, **f}, ensure_ascii=False), flush=True)


TOOLS = [
    {"type": "function", "function": {"name": "get_negotiation", "description": "Read the negotiation.",
     "parameters": {"type": "object", "properties": {"negotiation_id": {"type": "string"}},
                    "required": ["negotiation_id"]}}},
    {"type": "function", "function": {"name": "propose", "description": "Offer a whole-number price. Ends your turn.",
     "parameters": {"type": "object", "properties": {"negotiation_id": {"type": "string"},
                    "price": {"type": "integer"}, "note": {"type": "string"}},
                    "required": ["negotiation_id", "price"]}}},
]
STUB = {"get_negotiation": {"negotiation_id": "neg_probe", "item": "a desk lamp", "your_role": "buyer",
                            "status": "open", "turn": "buyer", "your_turn": True, "moves_left": 8,
                            "price": None, "moves": []},
        "propose": {"ok": True, "move": {"n": 1, "by": "buyer", "act": "propose", "price": 30, "note": ""},
                    "status": "open", "moves_left": 7}}

llm = LLM(emit)
messages = [
    {"role": "system", "content": "You are the buyer of a desk lamp. You can pay at most 45."},
    {"role": "user", "content": "Negotiation id: neg_probe. It is your turn: call get_negotiation, "
                                "then make exactly one move, then reply with the JSON summary."},
]
emit("probe_start", model=MODEL, provider=PROVIDER, reasoning=REASONING, response_format=FINAL["json_schema"]["name"])
ok_tools, final = 0, None
for step in range(6):
    resp = llm.chat(messages, TOOLS, tool_choice=sys.argv[1] if len(sys.argv) > 1 else "auto")
    msg = resp.choices[0].message
    emit("llm_response", step=step + 1, provider=getattr(resp, "provider", None),
         finish=resp.choices[0].finish_reason, content=msg.content,
         tool_calls=[(c.function.name, c.function.arguments) for c in msg.tool_calls or []],
         usage=resp.usage.model_dump() if resp.usage else None)
    messages.append(msg.model_dump(exclude_none=True))
    if not msg.tool_calls:
        final = msg.content
        break
    for c in msg.tool_calls:
        ok_tools += 1
        messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(STUB.get(c.function.name, {}))})
try:
    parsed = json.loads(final) if final else None
    valid = (isinstance(parsed, dict) and set(parsed) == {"action", "price", "reason"})
except ValueError:
    parsed, valid = None, False
emit("probe_result", tool_calls=ok_tools, final=final, final_valid=valid, model_calls=llm.calls)
sys.exit(0 if valid and ok_tools else 1)
