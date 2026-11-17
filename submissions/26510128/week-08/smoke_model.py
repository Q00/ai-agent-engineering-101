"""Smoke-check the real local Ollama meta-harness, without trading."""
from model_harness import decide, MODEL, BASE_URL

if __name__ == "__main__":
    choice = decide(
        'Return one JSON object exactly like {"action":"select","sku":"TEST"}; never include prose.',
        {"purpose": "offline model compatibility check; no market network calls"},
        ("select",),
    )
    print(f"[model smoke] endpoint={BASE_URL} model={MODEL} action={choice['action']} OK")
