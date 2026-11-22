"""The seller logs a new task's opening so a two-star bomber can be identified."""

from __future__ import annotations

import json

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, SendMessageRequest
from google.protobuf.struct_pb2 import Struct

from r3.seller import OPENING_TEXT_CHARS, opening_note


def context_with(
    text: str, headers: dict[str, str], meta: dict[str, str]
) -> RequestContext:
    metadata = Struct()
    metadata.update(meta)
    message = Message(
        message_id="m1",
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
        metadata=metadata,
    )
    call = ServerCallContext(state={"headers": headers})
    return RequestContext(
        request=SendMessageRequest(message=message), call_context=call
    )


def test_opening_note_keeps_wording_metadata_and_hashes_authorization() -> None:
    secret = "Bearer very-secret-token"  # noqa: S105 - test value, not a key
    note = json.loads(
        opening_note(
            context_with(
                "Buy G054ff120 for order 7f3a, need grade A",
                {"Authorization": secret, "User-Agent": "sergio-buyer/1.0"},
                {"order": "7f3a"},
            )
        )
    )
    assert note["text"].startswith("Buy G054ff120")
    assert note["meta"] == {"order": "7f3a"}
    assert note["headers"]["user-agent"] == "sergio-buyer/1.0"
    assert note["headers"]["authorization"].startswith("sha256:")
    assert secret not in json.dumps(note)


def test_opening_note_trims_long_text_and_never_raises() -> None:
    note = json.loads(opening_note(context_with("x" * 1000, {}, {})))
    assert len(note["text"]) == OPENING_TEXT_CHARS
    broken = RequestContext(request=None, call_context=None)
    assert "text" in json.loads(opening_note(broken)) or "error" in json.loads(
        opening_note(broken)
    )
