"""Reading a message back: three ways, one return type.

The runner does not know which condition it is running. It hands a message
and the transcript so far to a reader and gets a `Reading` back, so the only
things that vary between the three conditions are which reader is bound and
what that reader costs.

  free        the whole transcript goes to the model, which labels the LAST
              message. Reading the force out of context is this condition's
              entire claim, so the context has to actually be there.
              cost: one reader call per message.
  tagged      a regex takes the performative off the front. The model is
              asked only for the price inside a propose, and only the message
              itself is sent: that call is a price extraction, not a reading
              of force.  cost: one reader call per propose.
  structured  json.loads.  cost: nothing.

What `ok=False` means, exactly: the layer could not tell WHICH of the four
acts the message was. That is the `format_errors` column. A propose whose
price could not be recovered is NOT a format error -- the act was read, the
content was not, and the episode carries on with no price on the table. That
distinction is not pedantry: it is the mechanism behind the reference run's
`open` episodes, where a JSON message carried `"price": null`, nothing was
recorded, and the other side's later accept-proposal had no price to match.
"""
import json
import re

from agents import ACTS

# longest first: `accept-proposal` must not be shadowed by a shorter alternative
_TAG = re.compile(r"^\s*[(\[]\s*(accept-proposal|reject-proposal|propose|refuse)\s*[)\]]",
                  re.IGNORECASE)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class Reading:
    """What the protocol layer made of one message."""

    def __init__(self, performative=None, price=None, ok=True, reader_calls=0, why=""):
        self.performative = performative
        self.price = price
        self.ok = ok
        self.reader_calls = reader_calls
        self.why = why

    def __repr__(self):
        return (f"performative={self.performative} price={self.price} "
                f"ok={int(self.ok)}" + (f" ({self.why})" if self.why else ""))


def render_transcript(transcript):
    """The conversation as the reader sees it, one message per line, the one
    being labelled last."""
    return "\n".join(f"{who}: {text.strip()}" for who, text in transcript)


def _as_price(value):
    """A price is a non-negative whole number or nothing. `results.csv` takes
    no decimals and no negatives, so a reader that answers 182.5 has not
    answered the question."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    m = re.search(r"\d+", str(value).replace(",", ""))
    return int(m.group()) if m else None


def _loads(text):
    """JSON out of a model reply: bare, fenced, or with a sentence after it.
    The reference run saw 26 of 115 structured messages put prose after the
    object, so the trailing-text case is the common one, not the exotic one."""
    if not text:
        return None
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _ask_reader(seen, ask, meter):
    """One reader call. `seen` is whatever the reader is shown: the whole
    transcript in `free`, a single message in `tagged`. Returns
    (performative, price), either of which may be None."""
    from agents import READER_SYSTEM
    reply = ask(READER_SYSTEM, [{"role": "user", "content": seen}], meter, role="reader")
    obj = _loads(reply)
    if not isinstance(obj, dict):
        return None, None
    act = str(obj.get("performative", "")).strip().lower()
    return (act if act in ACTS else None), _as_price(obj.get("price"))


def read_free(text, transcript, ask, meter):
    """Nothing in the message marks the force, so the model reads it out of
    the conversation. Every message costs a call."""
    act, price = _ask_reader(render_transcript(transcript), ask, meter)
    if act is None:
        return Reading(None, None, ok=False, reader_calls=1,
                       why="reader picked none of the four acts")
    return Reading(act, price, ok=True, reader_calls=1)


def read_tagged(text, transcript, ask, meter):
    """The tag is free to read. Only the price still costs a call, and only
    inside a propose."""
    m = _TAG.match(text or "")
    if not m:
        return Reading(None, None, ok=False, reader_calls=0, why="no performative tag")
    act = m.group(1).lower()
    if act != "propose":
        return Reading(act, None, ok=True, reader_calls=0)
    _, price = _ask_reader(text, ask, meter)
    return Reading(act, price, ok=True, reader_calls=1,
                   why="" if price is not None else "propose, no price recovered")


def read_structured(text, transcript, ask, meter):
    """A parser and nothing else. No model call, and a message that is not the
    agreed object cannot be read at all."""
    obj = _loads(text)
    if not isinstance(obj, dict):
        return Reading(None, None, ok=False, reader_calls=0, why="not a JSON object")
    act = str(obj.get("performative", "")).strip().lower()
    if act not in ACTS:
        return Reading(None, None, ok=False, reader_calls=0,
                       why=f"performative {act!r} is not one of the four")
    content = obj.get("content")
    price = _as_price(content.get("price")) if isinstance(content, dict) else None
    return Reading(act, price, ok=True, reader_calls=0,
                   why="" if act != "propose" or price is not None
                       else "propose with content.price null")


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}
