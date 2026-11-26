"""A deterministic stand-in for the model, for smoke tests of host + runner + server
without any API call. It speaks the same chat-completions shape the host reads.

Strategy (from the tool results it has seen): call get_negotiation, then
- buyer: accept if the seller's last price <= budget + `overshoot`, else propose an
  opening/raised price; seller: accept if the buyer's last price >= reserve, else
  propose a lowered price. `overshoot` > 0 makes the buyer try to accept above its
  budget, which exercises attempted/refused counting and the server refusal path.
"""
import json
import re
from types import SimpleNamespace as NS


class ScriptedLLM:
    def __init__(self, emit=None, overshoot: int = 0, lazy: bool = False):
        self.calls = 0
        self.overshoot = overshoot
        self.lazy = lazy      # first reply after reading: a summary with no move (host v2 nudge path)

    @staticmethod
    def _limit(messages):
        m = re.search(r"at most (\d+)|at least (\d+)", messages[0]["content"])
        return int(m.group(1) or m.group(2))

    def chat(self, messages, tools, tool_choice="auto"):
        self.calls += 1
        role = "buyer" if "You are the buyer" in messages[0]["content"] else "seller"
        limit = self._limit(messages)
        nid = re.search(r"Negotiation id: (\S+)\.", messages[1]["content"]).group(1)
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs:
            return self._call("get_negotiation", {"negotiation_id": nid})
        last = tool_msgs[-1]["content"]
        if last.startswith("not sent") or ('"ok": true' in last):
            return self._final({"action": "none", "price": None, "reason": "done"})
        if last.startswith("error:"):
            # a refusal: fall back to a move inside the limit
            return self._call("propose", {"negotiation_id": nid, "price": limit, "note": "within my limit"})
        if self.lazy and messages[-1].get("role") == "tool" and last.lstrip().startswith("{") \
                and '"your_role"' in last:
            return self._final({"action": "propose", "price": None, "reason": "summary without a move"})
        if messages[-1].get("role") == "user":           # the host's nudge: read state again
            view = json.loads(last)
        else:
            view = json.loads(last)
        theirs = [m["price"] for m in view["moves"] if m["by"] != role and m["act"] == "propose"]
        if role == "buyer":
            if theirs and theirs[-1] <= limit + self.overshoot:
                return self._call("accept_proposal", {"negotiation_id": nid})
            mine = [m["price"] for m in view["moves"] if m["by"] == role and m["act"] == "propose"]
            price = min(limit, (mine[-1] + 10) if mine else max(1, limit - 30))
        else:
            if theirs and theirs[-1] >= limit:
                return self._call("accept_proposal", {"negotiation_id": nid})
            mine = [m["price"] for m in view["moves"] if m["by"] == role and m["act"] == "propose"]
            price = max(limit, (mine[-1] - 10) if mine else limit + 40)
        return self._call("propose", {"negotiation_id": nid, "price": price})

    def _call(self, name, args):
        tc = NS(id=f"call_{self.calls}", type="function",
                function=NS(name=name, arguments=json.dumps(args)))
        return self._resp(NS(content=None, tool_calls=[tc],
                             model_dump=lambda **k: {"role": "assistant", "tool_calls": [
                                 {"id": tc.id, "type": "function",
                                  "function": {"name": name, "arguments": tc.function.arguments}}]}),
                          "tool_calls")

    def _final(self, obj):
        text = json.dumps(obj)
        return self._resp(NS(content=text, tool_calls=None,
                             model_dump=lambda **k: {"role": "assistant", "content": text}), "stop")

    @staticmethod
    def _resp(msg, finish):
        return NS(choices=[NS(message=msg, finish_reason=finish)])
