# TOOLS.md

## clock

```json
{"name": "clock",
 "description": "Get the current date and time in UTC, as an ISO 8601 string. Takes no arguments. Use this whenever the user's request depends on 'now' (e.g. today's date, how long until/since something) rather than guessing or relying on training data.",
 "parameters": {"type": "object", "properties": {}, "required": []}}
```

I wrote the description around one problem: an LLM has no reliable sense of "now" — its training cutoff is not today, and without a tool it will happily guess or reuse a stale date from the prompt (the original `notes.txt` had a hardcoded `2026-09-01` header that the model could have copied instead of asking). So the description does two things explicitly instead of leaving them implicit:

1. **States the format up front** ("ISO 8601 string", "UTC") so the model doesn't need a round trip to discover it and can parse/compare the result without guessing a timezone.
2. **Tells the model *when* to reach for it**, not just what it does — "use this whenever the request depends on 'now' ... rather than guessing." Purely descriptive tool docs ("returns the current time") tell the model *what* the tool returns but not *why it should prefer calling the tool over answering from memory*, which is the actual failure mode I wanted to prevent. Adding the trigger condition directly in the description moved the model from sometimes fabricating a date to consistently calling `clock` first.

I also declared an explicit empty `parameters` schema (`"properties": {}, "required": []}`) rather than omitting `parameters` entirely, to keep the shape consistent with `calculator` and `read_file` and avoid relying on undocumented default behavior from whichever backend OpenRouter routes the call to.
