"""Fake LLM client for M1/M2. Returns scripted replies; never touches the network."""


class FakeLLM:
    model = "fake"

    def __init__(self, replies):
        self.replies = list(replies)   # str (raw reply) or Exception instance (raised)
        self.calls = []

    def complete(self, prompt, max_tokens=800):
        self.calls.append(prompt)
        r = self.replies.pop(0) if self.replies else "{}"
        if isinstance(r, Exception):
            raise r
        return {"text": r, "tokens_in": len(prompt) // 4, "tokens_out": len(r) // 4}
