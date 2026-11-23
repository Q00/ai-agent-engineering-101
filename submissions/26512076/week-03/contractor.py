import json
import time

from tools_shared import Chat


BID_SYSTEM = """
You are contractor {name} in a contract net.
Your skill is: {skill}.

You will receive one task announcement.
Decide whether to bid based on whether the task matches your skill.

Reply with exactly one JSON object and nothing else:
{{
  "bid": true,
  "confidence": 85,
  "reason": "one short sentence"
}}

Rules:
- "bid" must be true or false.
- "confidence" must be a number from 0 to 100.
- "reason" must be one short sentence.
- Do not perform the task.
- Do not choose the winner.

{extra_instruction}
""".strip()


ANNOUNCEMENT = """
TASK-ANNOUNCEMENT
contract-id: {task_id}
task-description: {desc}
eligibility: Bid only if the task matches your skill.
reply-format: JSON with bid, confidence, and reason.
""".strip()


def get_bid(name, skill, task_id, desc, meter, extra_instruction=""):
    system_prompt = BID_SYSTEM.format(
        name=name,
        skill=skill,
        extra_instruction=extra_instruction
    )

    user_message = ANNOUNCEMENT.format(
        task_id=task_id,
        desc=desc
    )

    chat = Chat(
        system=system_prompt,
        meter=meter,
        tools=False
    )

    chat.add_user(user_message)
    reply = chat.send()
    time.sleep(2.1)
    raw = reply.text.strip()

    try:
        parsed = json.loads(raw)

        if not all(key in parsed for key in ("bid", "confidence", "reason")):
            raise ValueError("missing required field")

        if not isinstance(parsed["bid"], bool):
            raise ValueError("bid must be boolean")

        confidence = parsed["confidence"]

        if isinstance(confidence, bool) or not isinstance(
            confidence, (int, float)
        ):
            raise ValueError("confidence must be numeric")

        if not 0 <= confidence <= 100:
            raise ValueError("confidence must be between 0 and 100")

        if not isinstance(parsed["reason"], str):
            raise ValueError("reason must be a string")

        return {
            "contractor": name,
            "bid": parsed["bid"],
            "confidence": confidence,
            "reason": parsed["reason"],
            "raw": raw,
            "parse_failed": False
        }

    except (json.JSONDecodeError, ValueError, TypeError):
        return {
            "contractor": name,
            "bid": False,
            "confidence": 0,
            "reason": "Unparseable or invalid bid response",
            "raw": raw,
            "parse_failed": True
        }