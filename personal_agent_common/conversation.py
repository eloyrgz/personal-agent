"""Conversation/session helpers shared by the router and OpenAI bridge."""

import hashlib
import uuid
from typing import Any


SESSION_HEADER_NAMES = (
    "x-conversation-id",
    "x-session-id",
    "x-openwebui-conversation-id",
    "x-openwebui-session-id",
    "conversation-id",
    "session-id",
)

UTILITY_MARKERS = (
    "generate a concise",
    "generate 1-3 broad tags",
    "suggest 3-5 relevant follow-up",
    "generate a suitable emoji",
)


def is_utility_request(messages: list[dict[str, Any]]) -> bool:
    return any(
        any(marker in str(message.get("content", "")).lower() for marker in UTILITY_MARKERS)
        for message in messages
    )


def derive_stable_conversation_id(messages: list[dict[str, Any]], prefix: str) -> str:
    """Use the first user message as a stable fallback session anchor."""
    for message in messages:
        if message.get("role") == "user":
            digest = hashlib.sha256(str(message.get("content", "")).encode("utf-8")).hexdigest()[:16]
            return f"{prefix}-{digest}"
    return f"{prefix}-{uuid.uuid4()}"


def resolve_conversation_id(
    request: dict[str, Any],
    headers: Any,
    messages: list[dict[str, Any]],
    prefix: str,
) -> str:
    request_conversation_id = request.get("conversation_id")
    if request_conversation_id:
        return request_conversation_id

    for header_name in SESSION_HEADER_NAMES:
        header_value = headers.get(header_name)
        if header_value:
            return header_value

    return derive_stable_conversation_id(messages, prefix)