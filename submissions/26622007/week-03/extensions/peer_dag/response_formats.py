"""Public phase contracts for API structured output. No file reads or answer keys."""
from core import DIMENSIONS, WORKERS

POLICY = "phase-json-schema-v2"


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def text_schema(limit):
    # Some grammar backends use full matching, unlike JSON Schema's substring search.
    # Bare \S can then force exactly one character. Explicitly allow the whole text.
    return {"type": "string", "minLength": 1, "maxLength": limit,
            "pattern": "^[\\s\\S]*\\S[\\s\\S]*$"}


def response_format(phase, payload):
    if phase == "propose":
        identifier = {"type": "string", "pattern": "^[a-zA-Z][a-zA-Z0-9_-]{0,79}$"}
        names = {"type": "array", "items": identifier, "maxItems": 20, "uniqueItems": True}
        step = object_schema({"id": identifier, "goal": text_schema(12000),
                              "acceptance": text_schema(12000), "depends_on": names,
                              "reads": names, "writes": names})
        schema = object_schema({
            "bid": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 100},
            "reason": text_schema(2000),
            "plan": object_schema({
                "mode": {"type": "string", "enum": ["execute", "delegate"]},
                "actions": {"type": "array", "items": text_schema(1500), "minItems": 1, "maxItems": 8},
                "steps": {"type": "array", "items": step, "maxItems": payload["max_steps"]}})})
        # Mode/steps consistency, depth, references and cycles still require local validation.
    elif phase == "review":
        candidates = payload["candidates"]
        if not candidates or not set(candidates) <= set(WORKERS):
            raise ValueError("review needs known candidate IDs")
        score = object_schema({name: {"type": "integer", "minimum": 0, "maximum": 2}
                               for name in DIMENSIONS})
        schema = object_schema({"scores": object_schema({worker: score for worker in sorted(candidates)})})
    elif phase in ("execute", "synthesize"):
        # Keys are chosen by workers from the public task; gold facts are never injected.
        scalar = {"anyOf": [{"type": "string", "maxLength": 2000}, {"type": "boolean"},
                             {"type": "number", "minimum": -1e100, "maximum": 1e100}]}
        schema = object_schema({
            "summary": text_schema(20000),
            "facts": {"type": "object", "maxProperties": 80, "propertyNames": text_schema(100),
                      "additionalProperties": scalar},
            "evidence": {"type": "array", "items": text_schema(3000), "minItems": 1, "maxItems": 30}})
    else:
        raise ValueError("unknown structured output phase")
    return {"type": "json_schema", "json_schema": {
        "name": f"peer_{phase}_v2", "strict": True, "schema": schema}}
