"""Model access for the week-03 contract net.

Trimmed from week-02's tools_shared.py. Week 03 needs one thing the week-02
Chat class wraps in machinery it does not need here: a single system+user
call with no tools and no conversation to accumulate. A contractor sees one
announcement and answers once, so there is nothing to carry forward.

What is kept from week 02: the provider switch on ANTHROPIC_API_KEY, the
AGENT_MODEL override, and Meter as the one place tokens and calls are counted.

What is added: the sampling settings are pinned and recorded, which week-02's
Chat did not do. They differ by provider, and the difference is not a detail:

  anthropic/claude-sonnet-5 — temperature, top_p and top_k are REMOVED on this
    model; sending temperature returns a 400 and the installed SDK does not
    accept the argument at all. There is no value to fix and none to read back.
    The lecture's instruction for exactly this case is to record "직접 설정
    불가, 내부 값 미확인" rather than guess a number. What is fixed instead is
    output_config.effort, which is where token spend is now controlled, plus
    thinking disabled so a bid is one flat classification call.

  openai/OpenRouter — temperature is still accepted and is pinned to 0.

Both paths report what they actually did in run_header(), so the report's
setup section states the truth for whichever provider produced the numbers.
"""

import os

# openai path only. Ignored by the anthropic path, which cannot set it.
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
# anthropic path only. Where token spend is controlled now that sampling is gone.
EFFORT = os.environ.get("AGENT_EFFORT", "low")
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "300"))

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-5" if PROVIDER == "anthropic" else "gpt-4o-mini")

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            from openai import OpenAI
            _client = OpenAI()
    return _client


def client():
    """The provider client. Public so agents.py can make tool-using calls
    without duplicating the provider switch or the lazy construction."""
    return _get_client()


class Meter:
    """Tokens and model calls, counted in one place.

    Same shape as week 02, including the same limitation: add() sums input
    and output into one counter, so the split cannot be recovered afterwards.
    Week 02's report had to give cost as a range for this reason. Kept as is
    so the two weeks' token figures mean the same thing.
    """

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


def call_model(system: str, user: str, meter: Meter) -> str:
    """One model call. Returns the reply text, unparsed and untruncated.

    Untruncated on purpose. Week 02's run_early.py cut each logged reply to
    300 characters and the line that decided a run fell past the cut, so the
    log did not show the evidence for its own verdict. Truncation here, if
    any, belongs to the logger, not to this function.
    """
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            thinking={"type": "disabled"},
            system=system, messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")

    resp = _get_client().chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0),
              getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""


def run_header() -> str:
    """The first line of every log file: what produced the numbers below it."""
    import platform
    try:
        if PROVIDER == "anthropic":
            import anthropic
            sdk = f"anthropic {anthropic.__version__}"
        else:
            import openai
            sdk = f"openai {openai.__version__}"
    except Exception:
        sdk = "sdk version unavailable"
    if PROVIDER == "anthropic":
        sampling = (f"temperature=NOT_SETTABLE(sampling removed on {MODEL}; "
                    f"internal value unknown) effort={EFFORT} thinking=disabled")
    else:
        sampling = f"temperature={TEMPERATURE}"
    return (f"provider={PROVIDER} model={MODEL} {sampling} "
            f"max_tokens={MAX_TOKENS} sdk={sdk} python={platform.python_version()}")
