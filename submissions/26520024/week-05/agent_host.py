"""The model selects tools; this host alone holds credentials and sends HTTP."""
import json
from market_server import strict_json, MOVES

MAX_DECISIONS = 6


def run_turn(client, model, role, scenario, nid, slot, prompts, emit):
    discovery = client.rpc("server/discover")
    if "tools" not in discovery["capabilities"]:
        raise RuntimeError("Market does not advertise tools")
    schemas = client.rpc("tools/list")["tools"]
    names = {tool["name"] for tool in schemas}
    system = prompts["roles"][role].format(item=scenario["item"], limit=scenario["budget" if role == "buyer" else "reserve"])
    system += prompts["common"] + json.dumps(schemas, sort_keys=True)
    history = [dict(role="user", content="Your negotiation_id is {}. It is your turn (host slot {}).".format(nid, slot))]
    for decision in range(1, MAX_DECISIONS + 1):
        emit("decision_start", host_slot=slot, role=role, decision=decision)
        raw = model(system, history, purpose=role)
        history.append(dict(role="assistant", content=raw))
        try:
            action = strict_json(raw)
            if (not isinstance(action, dict) or set(action) != {"name", "arguments", "reason"}
                    or not isinstance(action["name"], str) or action["name"] not in names
                    or not isinstance(action["arguments"], dict) or not isinstance(action["reason"], str)):
                raise ValueError("invalid action shape")
        except (ValueError, TypeError):
            emit("invalid_action", host_slot=slot, role=role, raw=raw)
            history.append(dict(role="user", content='{"error":"invalid_action","message":"Return exactly one JSON object with an available name, arguments object, and reason string."}'))
            continue
        emit("selected_action", host_slot=slot, role=role, decision=decision, action=action)
        result = client.call(action["name"], action["arguments"])
        history.append(dict(role="user", content=json.dumps(result)))
        if action["name"] in MOVES and not result["isError"]:
            emit("host_turn_end", host_slot=slot, role=role, moved=True, decisions=decision)
            return True
    emit("host_turn_end", host_slot=slot, role=role, moved=False, decisions=MAX_DECISIONS)
    return False
