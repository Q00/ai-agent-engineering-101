import json

from protocol import ACTS, parse_structured, split_tagged
from reader_prompts import FREE_READER_PROMPT, PRICE_READER_PROMPT
from tools_shared import Chat


class MessageReader:
    def __init__(self, meter, log=print):
        self.meter = meter
        self.log = log
        self.reader_calls = 0

    def ask_reader(self, prompt, conversation):
        chat = Chat(prompt, self.meter, tools=False)
        chat.add_user(json.dumps(conversation, ensure_ascii=False))

        self.reader_calls += 1
        reply = chat.send()
        self.log(f"[reader raw] {reply.text}")

        try:
            result = json.loads(reply.text)
        except json.JSONDecodeError:
            return None

        if not isinstance(result, dict):
            return None

        return result

    def read(self, condition, conversation):
        text = conversation[-1]["text"]

        if condition == "structured":
            result = parse_structured(text)

        elif condition == "tagged":
            tagged = split_tagged(text)

            if tagged is None:
                result = None
            elif tagged["performative"] != "propose":
                result = {
                    "performative": tagged["performative"],
                    "price": None,
                }
            else:
                answer = self.ask_reader(
                    PRICE_READER_PROMPT, conversation
                )
                price = answer.get("price") if answer is not None else None
                result = (
                    {"performative": "propose", "price": price}
                    if type(price) is int
                    else None
                )

        elif condition == "free":
            answer = self.ask_reader(
                FREE_READER_PROMPT, conversation
            )
            result = self.validate_free(answer)

        else:
            raise ValueError(f"Unknown condition: {condition}")

        self.log(f"[parse result] {json.dumps(result)}")
        return result

    @staticmethod
    def validate_free(answer):
        if answer is None:
            return None

        act = answer.get("performative")
        if not isinstance(act, str) or act not in ACTS:
            return None

        price = None
        if act == "propose":
            price = answer.get("price")
            if type(price) is not int:
                return None

        return {"performative": act, "price": price}