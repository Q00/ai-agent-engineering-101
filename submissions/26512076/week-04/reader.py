import json

from model_client import call_model


READER_SYSTEM_PROMPT = """
You are a protocol reader for a price negotiation.

Classify the final message as exactly one of:
- propose
- accept-proposal
- reject-proposal
- refuse

If the final message proposes a price, extract that proposed price as an integer.
Otherwise, use null for price.

You must choose one of the four performatives.
Return exactly one JSON object with no additional text:

{"performative": "propose", "price": 40}
"""


def reader_call(history, message):
    conversation = []

    for entry in history:
        conversation.append(
            f'{entry["role"]}: {entry["content"]}'
        )

    conversation_text = "\n".join(conversation)

    reader_history = [
        {
            "role": "user",
            "content": (
                f"Conversation so far:\n{conversation_text}\n\n"
                f"Final message to classify:\n{message}"
            ),
        }
    ]

    result = call_model(
        READER_SYSTEM_PROMPT,
        reader_history,
    )

    # JSON인지 즉시 검사하되 문자열 그대로 반환
    json.loads(result)
    return result